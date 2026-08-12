"""The Remotive adapter.

Verified against the live API on 2026-08-10, and two of these facts are worse than the project's
earlier notes recorded:

- `GET https://remotive.com/api/remote-jobs` returns exactly **20 postings**, always. `limit`,
  `search`, `category` and `company_name` are all accepted and all ignored, and the payload reports
  `total-job-count: 20` while the site itself lists thousands. So this is a window-only board: it
  shows the newest slice of a much larger corpus and describes that slice as the whole thing.
- The payload is a dict with the jobs under `jobs`, alongside a legal notice and a domain-move
  warning at keys beginning with digits.

What makes it worth having anyway is `candidate_required_location`: a comma-separated place list
("Worldwide", "USA, Canada, USA timezones", "Americas, Europe, Israel"). That is real eligibility
data, so Remotive joins Himalayas as a board the classifier can reason about precisely rather than
one it has to read prose from.

`salary` is free text, and the first genuine producer of `SalaryRange.note`. The board states the
period in the string, which makes it a surer signal than the magnitude inference Himalayas needs.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx

from job_seeker.domain.models import (
    CurrencySource,
    EligibilityHints,
    Job,
    SalaryPeriod,
    SalaryRange,
    SearchQuery,
    SourceResult,
)
from job_seeker.domain.timezones import offsets_for_band
from job_seeker.infrastructure.sources import base
from job_seeker.infrastructure.sources.salary import salary_from_bounds
from job_seeker.infrastructure.sources.scanning import collect

_API_URL = "https://remotive.com/api/remote-jobs"

# The board writes "$" and never names a currency. It is US-centric, so USD is this adapter's
# inference about its own board rather than something the board published.
_CURRENCY = "USD"

# Phrases that mean the figures are not base pay. "OTE" is on-target earnings, commission
# included, so reading it as salary overstates the role.
_NOT_BASE_PAY = ("ote", "commission", "bonus", "equity")
_HOURLY = re.compile(r"/\s*(hour|hr)\b|\bper hour\b|\bhourly\b", re.IGNORECASE)
# A figure with an embedded comma followed by fewer than three digits is European decimal
# notation ("31,2k" is 31.2k). Read as a thousands separator it is an order of magnitude out.
_AMBIGUOUS_DECIMAL = re.compile(r"\d,\d{1,2}(?!\d)")
# "$150k", "$45,000", "$17". The k suffix and the grouping commas are both optional.
_FIGURE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)\s*([kK])?")
# Below this, an unmarked figure is not a plausible annual salary in USD, so the period is left
# unknown rather than assumed. Above it, the board's own strings are consistently annual.
_ANNUAL_FLOOR = 1_000.0


class RemotiveSource:
    """Fetches Remotive postings and normalizes them into canonical `Job`s."""

    name = "remotive"

    def is_available(self) -> bool:
        # No credential, no optional dependency, and no I/O.
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Fetch the window, normalize, and report. Never raises: a failure is a SourceResult.error."""
        try:
            with base.build_client() as client:
                payload = base.get_json(client, _API_URL)
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")

        records = payload.get("jobs") if isinstance(payload, dict) else payload
        records = records if isinstance(records, list) else []
        scan = collect([r for r in records if isinstance(r, dict)], _normalize, query)

        # Always truncated, and not because we stopped early. The API hands back a 20-posting
        # window of a corpus in the thousands and calls it the total, so a run that read all of it
        # still saw a sliver. Reporting a complete scan here would be the precise lie
        # `SourceCoverage` exists to prevent.
        return SourceResult(source=self.name, jobs=scan.jobs, scanned=scan.scanned, truncated=True)


def _normalize(record: dict[str, Any]) -> Job | None:
    """One API record into a canonical Job, or None if it is unusable.

    Every access is defended: `fetch` runs in a thread-pool worker and must not raise on an
    untrusted payload.
    """
    title = str(record.get("title") or "").strip()
    url = str(record.get("url") or "").strip()
    if not title or not url:
        return None

    return Job(
        title=title,
        company=str(record.get("company_name") or "").strip(),
        url=url,
        source=RemotiveSource.name,
        description=base.clean_html(str(record.get("description") or "")),
        location=str(record.get("candidate_required_location") or "").strip(),
        salary=_salary(str(record.get("salary") or "")),
        posted_at=_published(record.get("publication_date")),
        employment_type=str(record.get("job_type") or "").replace("_", " ").strip(),
        hints=_hints(record),
    )


def _published(value: Any) -> datetime | None:
    """The ISO timestamp as an aware UTC datetime, or None.

    Remotive sends "2026-08-08T21:48:06", naive ISO, where the other boards send epoch seconds.
    Stamping it UTC here matters because a naive datetime compared against an aware `now` raises
    during age filtering, single-threaded, taking the whole run with it.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _hints(record: dict[str, Any]) -> EligibilityHints:
    """`candidate_required_location` as structured restrictions.

    A comma-separated list, reported as the board wrote it: the classifier normalizes and expands
    places itself, and an adapter that pre-interpreted "Worldwide" would be deciding eligibility
    rather than reporting a fact.

    The list holds two kinds of value, which is this board's dialect and so is settled here. Most
    are places ("Germany", "Europe", "Worldwide"), but some are timezone bands: four of twenty
    postings in one live window carried "European timezones" or "USA timezones". A band is not a
    place and matches no country, so left in the place list it pushes a posting toward
    `excluded-location` on the strength of a value nobody read. For a band the seeker sits inside
    that is a job refused: someone at UTC-6 meets "USA timezones".

    An empty string is `None`, not `()`, on both fields. The board saying nothing and the board
    saying "no restriction" are different claims, and collapsing them is the failure the whole
    eligibility layer is built around.
    """
    raw = str(record.get("candidate_required_location") or "").strip()
    if not raw:
        return EligibilityHints()
    places: list[str] = []
    offsets: set[float] = set()
    for value in (part.strip() for part in raw.split(",")):
        if not value:
            continue
        band = offsets_for_band(value)
        if band is None:
            places.append(value)
        else:
            offsets.update(band)
    return EligibilityHints(
        location_restrictions=tuple(places) or None,
        timezone_restrictions=tuple(sorted(offsets)) or None,
    )


def _salary(text: str) -> SalaryRange | None:
    """The board's pay text as a `SalaryRange`, or None when it published none.

    **The text always survives in `note`, whether or not the figures are read.** A seeker sees what
    the board actually offered even when this declines to parse it, and declining is the common
    case by design: a wrong salary is worse than an unparsed one.
    """
    text = text.strip()
    if not text:
        return None
    note = SalaryRange(currency=None, currency_source=None, note=text)
    if any(marker in text.lower() for marker in _NOT_BASE_PAY):
        return note  # on-target earnings are not base pay
    if _AMBIGUOUS_DECIMAL.search(text):
        return note  # "31,2k" is European notation and would parse an order of magnitude out
    figures = [_figure(match) for match in _FIGURE.finditer(text)]
    if not figures or any(f is None for f in figures):
        return note
    period = _period([f for f in figures if f is not None], text)
    if period is None:
        return note
    low, *rest = [f for f in figures if f is not None]
    return (
        salary_from_bounds(
            low,
            rest[0] if rest else None,
            currency=_CURRENCY,
            currency_source=CurrencySource.ASSUMED,
            period=period,
            note=text,
        )
        or note
    )


def _figure(match: re.Match[str]) -> float | None:
    """One "$..." figure as a number, or None if it will not read cleanly."""
    digits, suffix = match.group(1), match.group(2)
    try:
        value = float(digits.replace(",", ""))
    except ValueError:
        return None
    return value * 1000 if suffix else value


def _period(figures: list[float], text: str) -> SalaryPeriod | None:
    """What the figures are quoted per, or None when it cannot be established.

    The board usually says: "/hour" and "/hr" are explicit and settle it outright, which makes this
    surer than the magnitude bands Himalayas needs. Without a marker, only a figure large enough to
    be a plausible annual salary is treated as one; anything smaller is left unknown rather than
    guessed, since an unmarked "$50" could be hourly, daily, or a typo.
    """
    if _HOURLY.search(text):
        return SalaryPeriod.HOUR
    return SalaryPeriod.YEAR if min(figures) >= _ANNUAL_FLOOR else None

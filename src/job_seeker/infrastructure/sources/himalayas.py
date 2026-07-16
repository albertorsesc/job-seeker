"""The Himalayas adapter.

Himalayas is the eligibility star: every posting carries structured `locationRestrictions` and
`timezoneRestrictions`, so its jobs arrive with real `EligibilityHints` rather than text the
classifier has to guess from. The tradeoffs, all verified against the live API:

- `limit` caps at 20 per page and the filter params are ignored, so the API always returns the
  full recency-ordered feed (~98k postings) and we paginate and filter client-side.
- `companyName` is the literal string "name" for every record; `companySlug` is the real
  identifier, so the company is derived from the slug.
- `seniority` is a list, timezone restrictions are ints, and dates are Unix epoch seconds.

Recency ordering is what makes client-side age filtering cheap: once a full page has nothing
inside the age window, everything after it is older too, so the scan stops there.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from job_seeker.domain.models import EligibilityHints, Job, SearchQuery, SourceResult
from job_seeker.infrastructure.sources import base

_API_URL = "https://himalayas.app/jobs/api"
_PAGE_SIZE = 20  # the API caps a page here regardless of what `limit` asks for
_SCAN_CAP = 2000  # a politeness ceiling: never walk the whole six-figure feed on one query
_PLACEHOLDER_COMPANY = "name"  # what the API returns in companyName for every record


class HimalayasSource:
    """Fetches Himalayas postings and normalizes them into canonical `Job`s."""

    name = "himalayas"

    def __init__(
        self,
        *,
        page_delay: float = 0.15,
        scan_cap: int = _SCAN_CAP,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # page_delay keeps the scan polite; sleep is injectable so tests do not wait.
        self._page_delay = page_delay
        self._scan_cap = scan_cap
        self._sleep = sleep

    def is_available(self) -> bool:
        # No credential, no optional dependency, and no I/O: Himalayas is always usable.
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Paginate, normalize, and report. Never raises: a failure is a SourceResult.error."""
        cutoff = _age_cutoff(query.max_age_days)
        jobs: list[Job] = []
        scanned = 0
        truncated = False
        offset = 0

        try:
            with base.build_client() as client:
                while True:
                    page = self._page(client, offset)
                    if not page:
                        break  # feed exhausted: coverage is complete within the window
                    scanned += len(page)

                    kept_any = False
                    parsed_any = False
                    records_after_break = 0
                    for index, record in enumerate(page):
                        job = _normalize(record)
                        if job is None:
                            continue  # unparseable: a data blip, not a signal about recency
                        parsed_any = True
                        if _is_stale(job, cutoff):
                            continue
                        kept_any = True
                        jobs.append(job)
                        if len(jobs) >= query.max_results_per_source:
                            records_after_break = len(page) - (index + 1)
                            break

                    if len(jobs) >= query.max_results_per_source:
                        # Truncated only if more could remain: records still on this page, or a
                        # full page, which means more pages follow. Filling the result exactly on
                        # the last record of a short final page leaves nothing, so not truncated.
                        truncated = records_after_break > 0 or len(page) >= _PAGE_SIZE
                        break
                    if parsed_any and not kept_any:
                        # Recency-ordered: a page of real postings all older than the window means
                        # everything after it is older too. An all-unparseable page is NOT this
                        # signal (it may be a transient blip with fresh jobs behind it), so it does
                        # not stop the scan; the scan cap and short-page checks still bound it.
                        break
                    if scanned >= self._scan_cap:
                        truncated = True  # stopped on the scan ceiling, not on running out
                        break
                    if len(page) < _PAGE_SIZE:
                        break  # short page is the last page
                    offset += _PAGE_SIZE
                    self._sleep(self._page_delay)
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")

        return SourceResult(source=self.name, jobs=jobs, scanned=scanned, truncated=truncated)

    def _page(self, client: httpx.Client, offset: int) -> list[dict[str, Any]]:
        payload = base.get_json(
            client, _API_URL, params={"limit": _PAGE_SIZE, "offset": offset}, sleep=self._sleep
        )
        jobs = payload.get("jobs") if isinstance(payload, dict) else payload
        return jobs if isinstance(jobs, list) else []


def _age_cutoff(max_age_days: int | None) -> datetime | None:
    if max_age_days is None:
        return None
    return datetime.now(UTC) - timedelta(days=max_age_days)


def _is_stale(job: Job, cutoff: datetime | None) -> bool:
    if cutoff is None or job.posted_at is None:
        return False  # no window, or no date to judge: keep it and let the seeker decide
    return job.posted_at < cutoff


def _normalize(record: Any) -> Job | None:
    """One API record into a canonical Job, or None if it is unusable.

    Returns None rather than raising for anything short of a usable posting, so one bad row
    cannot end a page or a run. Every field access below is defended, because the input is
    untrusted third-party JSON and `fetch` is contracted never to raise: a record that is not
    even a dict, a non-numeric salary, a null timezone are all things a board can send.
    """
    if not isinstance(record, dict):
        return None
    title = str(record.get("title") or "").strip()
    url = str(record.get("applicationLink") or record.get("guid") or "").strip()
    if not title or not url:
        return None

    return Job(
        title=title,
        company=_company(record),
        url=url,
        source=HimalayasSource.name,
        description=base.clean_html(str(record.get("description") or "")),
        salary=_salary(record),
        posted_at=base.to_utc_datetime(record.get("pubDate")),
        seniority=_seniority(record),
        employment_type=str(record.get("employmentType") or ""),
        hints=_hints(record),
    )


def _seniority(record: dict[str, Any]) -> str:
    """The seniority list joined to text. A stray string (not a list) yields "" rather than
    iterating its characters into "S, e, n, i, o, r"."""
    values = record.get("seniority")
    return ", ".join(str(s) for s in values) if isinstance(values, list) else ""


def _company(record: dict[str, Any]) -> str:
    """The company, from the slug, because companyName is the literal "name" in every record.

    A genuine companyName is preferred when one ever appears, so the day the API is fixed this
    keeps working without a change.
    """
    name = str(record.get("companyName") or "").strip()
    if name and name.lower() != _PLACEHOLDER_COMPANY:
        return name
    slug = str(record.get("companySlug") or "").strip()
    return slug.replace("-", " ").title()


def _salary(record: dict[str, Any]) -> str:
    minimum = _as_number(record.get("minSalary"))
    maximum = _as_number(record.get("maxSalary"))
    currency = str(record.get("currency") or "").strip()
    if not minimum and not maximum:
        return ""
    if minimum and maximum and minimum != maximum:
        return f"{currency} {minimum:,} - {maximum:,}".strip()
    value = minimum or maximum
    return f"{currency} {value:,}".strip()


def _as_number(value: Any) -> int | float | None:
    """A salary as a number, or None. A string like "120k" formats fine as text but crashes
    `f"{x:,}"`, so coerce here rather than let it escape the never-raise contract."""
    return value if isinstance(value, int | float) and not isinstance(value, bool) else None


def _hints(record: dict[str, Any]) -> EligibilityHints:
    """Structured restrictions into hints. Himalayas always reports these, so they are known.

    A list (even empty) is a statement, so it becomes a tuple; a missing key would be unknown,
    so it stays None. On this board the keys are always present, so the None branch guards
    against a future shape change rather than today's data.
    """
    location = record.get("locationRestrictions")
    timezone = record.get("timezoneRestrictions")
    return EligibilityHints(
        location_restrictions=tuple(str(x) for x in location)
        if isinstance(location, list)
        else None,
        # Skip a non-numeric timezone entry rather than let float() raise. The field stays a
        # tuple (the board reported it); only the unusable element is dropped.
        timezone_restrictions=tuple(
            float(x) for x in timezone if isinstance(x, int | float) and not isinstance(x, bool)
        )
        if isinstance(timezone, list)
        else None,
    )

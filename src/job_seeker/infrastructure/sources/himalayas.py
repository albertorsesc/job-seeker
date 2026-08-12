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
from job_seeker.infrastructure.sources import base
from job_seeker.infrastructure.sources.salary import salary_from_bounds

_API_URL = "https://himalayas.app/jobs/api"
_PAGE_SIZE = 20  # the API caps a page here regardless of what `limit` asks for
_SCAN_CAP = 2000  # a politeness ceiling: never walk the whole six-figure feed on one query
_PLACEHOLDER_COMPANY = "name"  # what the API returns in companyName for every record
# Bands for classifying a figure's period, measured from live data. See `_salary_period`.
_HOURLY_CEILING = 200.0
_ANNUAL_FLOOR = 50_000.0
# The annual floor is a magnitude, so it only means anything in a currency of comparable
# denomination. A monthly 3,000,000 COP or 400,000 JPY clears 50,000 and would read as annual, so
# the upper band applies only where it was measured. Everything else falls through to unknown,
# which is the safe direction. Extend this only with live evidence for the currency added.
_CALIBRATED_CURRENCIES = frozenset({"USD", "EUR", "CAD", "GBP", "PLN"})


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
        cutoff = base.age_cutoff(query.max_age_days)
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
                        if base.is_stale(job.posted_at, cutoff):
                            continue
                        kept_any = True
                        jobs.append(job)
                        if len(jobs) >= query.scan_depth_per_source:
                            records_after_break = len(page) - (index + 1)
                            break

                    if len(jobs) >= query.scan_depth_per_source:
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


def _salary(record: dict[str, Any]) -> SalaryRange | None:
    """The figures the board published, with the currency it published alongside them.

    The currency is the only board-specific part: Himalayas reports one, so it is read rather than
    assumed. Everything else, including what to do with a figure the board should not have sent,
    is shared in `salary_from_bounds`.
    """
    currency = str(record.get("currency") or "").strip() or None
    return salary_from_bounds(
        record.get("minSalary"),
        record.get("maxSalary"),
        currency=currency,
        # Read from the payload, never supplied by this adapter, so it is always a published fact.
        currency_source=CurrencySource.PUBLISHED if currency else None,
        period=_salary_period(record.get("minSalary"), record.get("maxSalary"), currency),
    )


def _salary_period(minimum: Any, maximum: Any, currency: str | None) -> SalaryPeriod | None:
    """What a Himalayas figure is quoted per, or None when it cannot be established.

    Himalayas publishes no period field and its figures are genuinely mixed: an 85 and a 146,000
    arrive in the same currency on the same page. So this adapter infers, from magnitude, within
    bands wide enough that the inference is safe, and refuses to guess between them.

    Measured over 311 pay-bearing records across 30 live pages:

    - 47 records below 200, the largest being 150. No annual salary is 150 in any currency, and
      the titles are hourly work ("Aerospace Engineer 85-85 USD"). Treated as HOUR.
    - 243 records at or above 50,000, the smallest being exactly 50,000. Treated as YEAR.
    - 21 records in between, and that band is genuinely mixed: "Senior Software Engineer -Colombia"
      at 3,255-4,160 USD is monthly, "COB Claims Trainer" at 47,000-67,200 USD is annual, and
      14,000-16,000 PLN is monthly where 26,000-28,000 GBP is annual. Magnitude cannot separate
      them, so the answer is None and the figures simply do not annualize.

    **Known limit:** the upper band is calibrated on the currencies actually observed (USD, EUR,
    CAD, GBP, PLN). A monthly figure in a low-denomination currency, 3,000,000 COP or 400,000 JPY,
    exceeds 50,000 and would be read as annual. Narrow the band by currency if such a posting ever
    appears; erring toward None is the safe direction and this rule does not yet do it there.
    """
    figure = _first_figure(minimum, maximum)
    if figure is None:
        return None
    if figure < _HOURLY_CEILING:
        # Safe in any currency: no annual salary anywhere is under 200 units.
        return SalaryPeriod.HOUR
    if figure >= _ANNUAL_FLOOR and (currency or "").upper() in _CALIBRATED_CURRENCIES:
        return SalaryPeriod.YEAR
    return None


def _first_figure(minimum: Any, maximum: Any) -> float | None:
    """Whichever bound the board actually sent, for classifying the pair. Both bounds are quoted
    the same way, so either settles it."""
    for value in (minimum, maximum):
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None


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

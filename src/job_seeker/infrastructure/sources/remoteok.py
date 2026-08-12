"""The RemoteOK adapter.

RemoteOK is the counterpoint to Himalayas: it publishes no structured eligibility data, so its
jobs carry hints of None and the classifier reads the posting text. Verified against the live API:

- `GET https://remoteok.com/api` returns a flat list. The first element is legal boilerplate (a
  request to link back), not a job, and is skipped.
- One request, no pagination: the endpoint returns roughly the latest hundred postings.
- Jobs are keyed on `position` (title), `company`, `apply_url`, `epoch` (Unix seconds), and a
  free-text `location`. Salaries are integers, often zero for "unspecified".

There is no server-side filter, so the whole feed comes back and the pipeline narrows it. That is
why the relevance filter matters here: RemoteOK returns every category, not just engineering.
"""

from __future__ import annotations

from typing import Any

import httpx

from job_seeker.domain.models import (
    CurrencySource,
    Job,
    SalaryPeriod,
    SalaryRange,
    SearchQuery,
    SourceResult,
)
from job_seeker.infrastructure.sources import base
from job_seeker.infrastructure.sources.salary import salary_from_bounds
from job_seeker.infrastructure.sources.scanning import collect

_API_URL = "https://remoteok.com/api"

# What RemoteOK's `salary_min`/`salary_max` are quoted per. The board publishes no period field,
# so this is this adapter's declaration about its own board, the same kind of claim as the USD
# below.
#
# Evidence, and it is thin: a live sample of the full feed had pay on 1 of 100 records
# (50,000-210,000), consistent with annual and with the board's own salary filters, which are
# annual USD bands. No figure resembling an hourly or monthly rate has been observed. Revisit if a
# posting ever appears with a figure under about 1,000.
_SALARY_PERIOD = SalaryPeriod.YEAR


class RemoteOkSource:
    """Fetches RemoteOK postings and normalizes them into canonical `Job`s."""

    name = "remoteok"

    def is_available(self) -> bool:
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Fetch the feed, normalize, and report. Never raises: a failure is a SourceResult.error."""
        try:
            with base.build_client() as client:
                payload = base.get_json(client, _API_URL)
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")

        records = payload if isinstance(payload, list) else []
        # The boilerplate row and any non-dict are filtered out before the loop rather than inside
        # it, so `scanned` counts postings examined and not rows walked past, and so "a record
        # still remains" means a real posting remains.
        postings = [record for record in records if _is_job_record(record)]
        scan = collect(postings, _normalize, query)
        return SourceResult(
            source=self.name, jobs=scan.jobs, scanned=scan.scanned, truncated=scan.bounded
        )


def _is_job_record(record: Any) -> bool:
    """A posting, not the leading legal boilerplate or a stray non-dict. Boilerplate has no
    `position`, so this is content-based, not positional: it holds if RemoteOK ever drops or
    reshapes the boilerplate."""
    return isinstance(record, dict) and bool(str(record.get("position") or "").strip())


def _normalize(record: Any) -> Job | None:
    """One API record into a canonical Job, or None for boilerplate or an unusable record.

    Every access is defended: `fetch` runs in a thread-pool worker and must not raise on the
    untrusted feed (the first element is not a job, and a record may be any shape).
    """
    if not isinstance(record, dict):
        return None
    title = str(record.get("position") or "").strip()
    url = str(record.get("apply_url") or record.get("url") or "").strip()
    if not title or not url:
        return None

    return Job(
        title=title,
        company=str(record.get("company") or "").strip(),
        url=url,
        source=RemoteOkSource.name,
        description=base.clean_html(str(record.get("description") or "")),
        location=str(record.get("location") or "").strip(),
        salary=_salary(record),
        posted_at=base.to_utc_datetime(record.get("epoch")),
    )


def _salary(record: dict[str, Any]) -> SalaryRange | None:
    """The figures the board published, in USD.

    USD is asserted, not read: the /api endpoint exposes salary_min/max and no currency field. The
    assertion is this adapter's knowledge of its own board, which is why it is the one part not
    shared with `salary_from_bounds`.
    """
    return salary_from_bounds(
        record.get("salary_min"),
        record.get("salary_max"),
        currency="USD",
        currency_source=CurrencySource.ASSUMED,
        period=_SALARY_PERIOD,
    )

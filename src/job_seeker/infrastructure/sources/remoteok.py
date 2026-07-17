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

from job_seeker.domain.models import Job, SearchQuery, SourceResult
from job_seeker.infrastructure.sources import base

_API_URL = "https://remoteok.com/api"


class RemoteOkSource:
    """Fetches RemoteOK postings and normalizes them into canonical `Job`s."""

    name = "remoteok"

    def is_available(self) -> bool:
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Fetch the feed, normalize, and report. Never raises: a failure is a SourceResult.error."""
        cutoff = base.age_cutoff(query.max_age_days)
        try:
            with base.build_client() as client:
                payload = base.get_json(client, _API_URL)
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")

        records = payload if isinstance(payload, list) else []
        jobs: list[Job] = []
        scanned = 0
        truncated = False
        for index, record in enumerate(records):
            if not _is_job_record(record):
                continue  # the legal boilerplate, or a non-dict: not a posting to examine
            scanned += 1  # examined, the same meaning as Himalayas' scanned
            job = _normalize(record)
            if job is None or base.is_stale(job.posted_at, cutoff):
                continue
            jobs.append(job)
            if len(jobs) >= query.max_results_per_source:
                # Truncated only if a real posting still remains past the break. Deriving it from
                # position, not from a recount, is what keeps scanned and truncated in agreement.
                truncated = any(_is_job_record(r) for r in records[index + 1 :])
                break

        return SourceResult(source=self.name, jobs=jobs, scanned=scanned, truncated=truncated)


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


def _salary(record: dict[str, Any]) -> str:
    # USD is assumed, not read: the /api endpoint exposes salary_min/max but no currency field.
    minimum = _positive(record.get("salary_min"))
    maximum = _positive(record.get("salary_max"))
    if not minimum and not maximum:
        return ""  # RemoteOK uses 0 for "unspecified"
    if minimum and maximum and minimum != maximum:
        return f"USD {minimum:,} - {maximum:,}"
    value = minimum or maximum
    return f"USD {value:,}"


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None

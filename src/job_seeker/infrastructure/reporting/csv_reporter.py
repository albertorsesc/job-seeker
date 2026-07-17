"""Render a run as CSV: one row per ranked job, for a spreadsheet.

Flattens each ScoredJob to the fields a human scanning a sheet wants. The csv module quotes any
field containing a comma or newline, so a salary like "USD 150,000" or a description survives
intact. Presentation only; the rows are in the domain's rank order.
"""

from __future__ import annotations

import csv
import io

from job_seeker.domain.models import ScoredJob, SearchResult

_COLUMNS = ("rank", "fit", "eligibility", "reason", "title", "company", "source", "salary", "url")
# A cell beginning with one of these is executed as a formula by Excel/Sheets. Board data is
# untrusted, so a title like "=cmd|..." must be neutralized before it reaches a spreadsheet.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


class CsvReporter:
    """Serializes a SearchResult to CSV rows."""

    def render(self, result: SearchResult, /) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=_COLUMNS)
        writer.writeheader()
        for rank, scored in enumerate(result.jobs, start=1):
            writer.writerow(_row(rank, scored))
        return buffer.getvalue()


def _row(rank: int, scored: ScoredJob) -> dict[str, object]:
    job = scored.job
    return {
        "rank": rank,
        "fit": scored.fit.value,
        "eligibility": scored.eligibility.status.value,
        "reason": _safe(scored.eligibility.reason),
        "title": _safe(job.title),
        "company": _safe(job.company),
        "source": _safe(job.source),
        "salary": _safe(job.salary),
        "url": _safe(job.url),
    }


def _safe(cell: str) -> str:
    """Neutralize a spreadsheet-formula cell by prefixing an apostrophe, which a spreadsheet reads
    as "this is text". Leaves ordinary values untouched."""
    return f"'{cell}" if cell.startswith(_FORMULA_TRIGGERS) else cell

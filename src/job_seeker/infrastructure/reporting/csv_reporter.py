"""Render a run as CSV: one row per ranked job, for a spreadsheet.

Flattens each ScoredJob to the fields a human scanning a sheet wants. The csv module quotes any
field containing a comma or newline, so a matched breakdown like "python +3, rag +2" survives
intact. Presentation only; the rows are in the domain's rank order.
"""

from __future__ import annotations

import csv
import io

from job_seeker.domain.models import PostingHistory, SalaryRange, ScoredJob, SearchResult

_COLUMNS = (
    "rank",
    "fit",
    "matched",
    "relevance",
    "eligibility",
    "reason",
    "title",
    "company",
    "source",
    # Pay as separate columns rather than one formatted string. A spreadsheet can sort and filter
    # on a number and cannot on "USD 120,000 - 160,000", which is the single thing someone opening
    # a CSV of job postings most wants to do. `salary_note` carries the board's own words for the
    # boards that publish prose instead of figures.
    "salary_min",
    "salary_max",
    "currency",
    # Whether the board stated that currency or the adapter supplied it. Both look identical
    # otherwise, so a reader comparing across currencies cannot tell fact from inference.
    "currency_source",
    "salary_period",
    # The comparable columns, and the reason the period is tracked at all: an hourly figure and an
    # annual one sort against each other correctly only here. Empty when the period is unknown,
    # which is the honest answer rather than a number on an invented basis.
    "annual_min",
    "annual_max",
    "salary_note",
    "url",
    # Memory, appended rather than slotted in beside the other verdicts, so a spreadsheet or script
    # someone already built against this file keeps working: every existing column stays where it
    # was. `handle` is what a `job-seeker mark` command takes, which is why it travels with the row
    # rather than being something to look up.
    "handle",
    "new",
    "times_seen",
    "decision",
)
# A cell beginning with one of these is executed as a formula by Excel/Sheets. Board data is
# untrusted, so a title like "=cmd|..." must be neutralized before it reaches a spreadsheet.
_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
_HISTORY_COLUMNS = ("handle", "new", "times_seen", "decision")
# Keyed once so the empty row and the populated row cannot drift apart: DictWriter
# silently substitutes "" for a key one branch forgot.
_SALARY_COLUMNS = (
    "salary_min",
    "salary_max",
    "currency",
    "currency_source",
    "salary_period",
    "annual_min",
    "annual_max",
    "salary_note",
)


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
        "matched": _safe(_matched(scored.fit.matched)),
        "relevance": _safe(scored.relevance.reason),
        "eligibility": scored.eligibility.status.value,
        "reason": _safe(scored.eligibility.reason),
        "title": _safe(job.title),
        "company": _safe(job.company),
        "source": _safe(job.source),
        **_salary_cells(job.salary),
        "url": _safe(job.url),
        **_history_cells(scored.history),
    }


def _salary_cells(salary: SalaryRange | None) -> dict[str, object]:
    """Pay spread across its columns. Empty cells when the board published nothing.

    The figures go in as numbers, not strings, so a spreadsheet reads them as numbers. `_safe` is
    applied only to the free text, since that is the one part a board controls.
    """
    if salary is None:
        return dict.fromkeys(_SALARY_COLUMNS, "")
    return {
        "salary_min": salary.minimum if salary.minimum is not None else "",
        "salary_max": salary.maximum if salary.maximum is not None else "",
        "currency": _safe(salary.currency or ""),
        "currency_source": salary.currency_source.value if salary.currency_source else "",
        "salary_period": salary.period.value if salary.period else "",
        "annual_min": salary.annual_minimum if salary.annual_minimum is not None else "",
        "annual_max": salary.annual_maximum if salary.annual_maximum is not None else "",
        "salary_note": _safe(salary.note),
    }


def _matched(matched: dict[str, int]) -> str:
    """The fit breakdown as text: "python +3, rag +2", so the score explains itself in a cell."""
    return ", ".join(f"{pattern} +{weight}" for pattern, weight in matched.items())


def _safe(cell: str) -> str:
    """Neutralize a spreadsheet-formula cell by prefixing an apostrophe, which a spreadsheet reads
    as "this is text". Leaves ordinary values untouched."""
    return f"'{cell}" if cell.startswith(_FORMULA_TRIGGERS) else cell


def _history_cells(history: PostingHistory | None) -> dict[str, object]:
    """What memory knew, or empty cells when it could not answer.

    Empty rather than `new=False`: a column claiming a posting is not new, on a run where nothing
    could be determined, is a spreadsheet full of confident wrong answers. Blank is the honest cell
    and sorts to one end where a reader will notice it.
    """
    if history is None:
        return dict.fromkeys(_HISTORY_COLUMNS, "")
    return {
        "handle": history.handle,
        "new": history.is_new,
        "times_seen": history.times_seen,
        "decision": history.decision.value if history.decision else "",
    }

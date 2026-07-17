"""Driven adapters: render a result to JSON, CSV, or a self-contained HTML page.

Presentation only. A reporter decides how a result looks, never which postings are in it or
what order they take: that ranking already happened in the domain. A reporter that filters is
a bug, because it makes the JSON and the HTML disagree about what the run found.
"""

from job_seeker.application.ports import Reporter
from job_seeker.infrastructure.reporting.csv_reporter import CsvReporter
from job_seeker.infrastructure.reporting.html_reporter import HtmlReporter
from job_seeker.infrastructure.reporting.json_reporter import JsonReporter

_REPORTERS: dict[str, type[Reporter]] = {
    "json": JsonReporter,
    "csv": CsvReporter,
    "html": HtmlReporter,
}

FORMATS = tuple(_REPORTERS)


def reporter_for(fmt: str) -> Reporter:
    """The reporter for a format name, or a clear error listing the valid ones."""
    try:
        return _REPORTERS[fmt]()
    except KeyError:
        raise ValueError(f"unknown format {fmt!r}; choose one of {', '.join(FORMATS)}") from None


__all__ = ["FORMATS", "CsvReporter", "HtmlReporter", "JsonReporter", "Reporter", "reporter_for"]

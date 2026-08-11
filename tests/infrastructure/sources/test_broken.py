"""Covers `job_seeker.infrastructure.sources.broken`.

Not a board. A stand-in the composition root substitutes when a real board's factory raises, so a
single adapter's construction bug is reported as coverage rather than ending the run.
"""

from __future__ import annotations

from job_seeker.application.ports import JobSource
from job_seeker.domain.models import SearchQuery
from job_seeker.infrastructure.sources.broken import BrokenSource


def _accepts_source(source: JobSource) -> str:
    """Force mypy to prove conformance. A fake that is merely defined proves nothing."""
    return source.name


class TestBrokenSource:
    def test_it_satisfies_the_JobSource_port(self) -> None:
        assert _accepts_source(BrokenSource("jobspy", "RuntimeError: no credentials")) == "jobspy"

    def test_it_reports_the_failure_instead_of_raising(self) -> None:
        """The contract every source is held to: a failure is a SourceResult, not an exception."""
        result = BrokenSource("jobspy", "RuntimeError: no credentials").fetch(SearchQuery())
        assert result.failed
        assert result.source == "jobspy"
        assert result.error == "RuntimeError: no credentials"

    def test_it_carries_no_jobs_and_scanned_nothing(self) -> None:
        result = BrokenSource("jobspy", "boom").fetch(SearchQuery())
        assert result.jobs == []
        assert result.scanned == 0

    def test_it_is_not_available(self) -> None:
        assert BrokenSource("jobspy", "boom").is_available() is False

    def test_its_name_is_per_instance(self) -> None:
        """Why this one uses a property where every shipped board uses a class attribute: the name
        is whichever board failed, so it cannot be a constant."""
        assert BrokenSource("himalayas", "boom").name == "himalayas"
        assert BrokenSource("remoteok", "boom").name == "remoteok"

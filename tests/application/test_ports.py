"""Covers `job_seeker.application.ports`.

A Protocol is checked statically, so these tests carry their weight under mypy rather than at
runtime: `_accepts_*` exists to make the type checker prove that a plain class satisfies a port
without inheriting from it. That is the property the whole layout rests on, since it is what
lets an adapter in infrastructure satisfy the application without the arrow ever turning around.

For this to mean anything, mypy must check `tests/` as well as `src/`. `pyproject.toml` sets
`packages = ["job_seeker", "tests"]` for exactly that reason: with tests excluded, a fake that
had drifted from the port would sail through and the ports would be decoration.
"""

from __future__ import annotations

from job_seeker.application.ports import JobSource, ProfileProvider, Reporter
from job_seeker.domain.models import Job, SearchQuery, SearchResult, SourceResult
from job_seeker.domain.profile import Profile


class InMemorySource:
    """A board that answers from a list. Note it imports no port and inherits nothing."""

    def __init__(self, name: str, jobs: list[Job] | None = None) -> None:
        self._name = name
        self._jobs = jobs or []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        return SourceResult(source=self._name, jobs=list(self._jobs), scanned=len(self._jobs))


class UnavailableSource:
    """A source whose optional dependency is absent. Reports it instead of crashing."""

    @property
    def name(self) -> str:
        return "jobspy"

    def is_available(self) -> bool:
        return False

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        return SourceResult(source="jobspy", error="python-jobspy is not installed")


class StaticProfileProvider:
    def load(self) -> Profile:
        return Profile(name="Test Seeker")


class NullReporter:
    def render(self, result: SearchResult) -> str:
        return ""


def _accepts_source(source: JobSource) -> str:
    return source.name


def _accepts_provider(provider: ProfileProvider) -> Profile:
    return provider.load()


def _accepts_reporter(reporter: Reporter, result: SearchResult) -> str:
    return reporter.render(result)


class TestStructuralConformance:
    """Adapters satisfy a port by shape alone, never by importing or subclassing it.

    Every fake must pass through an `_accepts_*` call. A fake that is merely *defined* proves
    nothing: mypy only checks conformance where the object meets a port-typed parameter, so an
    unchecked fake can drift arbitrarily far from the port and the suite stays green.
    """

    def test_a_plain_class_satisfies_JobSource(self) -> None:
        assert _accepts_source(InMemorySource("himalayas")) == "himalayas"

    def test_a_source_that_reports_unavailable_also_satisfies_JobSource(self) -> None:
        assert _accepts_source(UnavailableSource()) == "jobspy"

    def test_a_plain_class_satisfies_ProfileProvider(self) -> None:
        assert _accepts_provider(StaticProfileProvider()).name == "Test Seeker"

    def test_a_plain_class_satisfies_Reporter(self) -> None:
        empty = SearchResult(query=SearchQuery())
        assert _accepts_reporter(NullReporter(), empty) == ""

    def test_a_source_carries_its_jobs_and_counts_what_it_scanned(self) -> None:
        job = Job(title="AI Engineer", company="Acme", url="https://x.co/1", source="himalayas")
        result = InMemorySource("himalayas", [job]).fetch(SearchQuery())
        assert result.jobs == [job]
        assert result.scanned == 1
        assert not result.failed


class TestUnavailableSourceReportsRatherThanRaises:
    def test_is_available_is_false_when_a_dependency_is_missing(self) -> None:
        assert UnavailableSource().is_available() is False

    def test_fetch_reports_the_failure_instead_of_raising(self) -> None:
        """A missing optional provider must degrade the run, not end it."""
        result = UnavailableSource().fetch(SearchQuery())
        assert result.failed
        assert result.jobs == []
        assert "not installed" in result.error

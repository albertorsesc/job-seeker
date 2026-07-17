"""Covers `job_seeker.application.orchestrator`.

The orchestrator is the combination spine: it fans sources out, merges the same posting seen on
several boards, scores and classifies each against the profile, drops what the seeker cannot hold,
and ranks the rest. Exercised with in-memory fake sources; no network.
"""

from __future__ import annotations

from job_seeker.application.orchestrator import JobSeeker
from job_seeker.domain.models import (
    EligibilityHints,
    Job,
    SearchQuery,
    SearchResult,
    SourceResult,
)
from job_seeker.domain.profile import EligibilityRules, LocationProfile, Profile


def _profile(**rules: object) -> Profile:
    return Profile(
        location=LocationProfile(country="Testland", timezone_utc_offset=-6.0),
        skills={r"\bpython\b": 3, r"\brag\b": 2},
        eligibility=EligibilityRules(eligible_regions=["testland", "latam"], **rules),  # type: ignore[arg-type]
    )


def _job(title: str, *, source: str, company: str = "Acme", url: str = "", **fields: object) -> Job:
    return Job(
        title=title,
        company=company,
        url=url or f"https://{source}/{title}".replace(" ", "-"),
        source=source,
        hints=fields.pop("hints", EligibilityHints(location_restrictions=())),  # type: ignore[arg-type]
        **fields,  # type: ignore[arg-type]
    )


class FakeSource:
    """A board that answers from a fixed list, or fails, or raises."""

    def __init__(self, name: str, result: SourceResult | None = None, raises: bool = False) -> None:
        self._name = name
        self._result = result if result is not None else SourceResult(source=name)
        self._raises = raises

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        if self._raises:
            raise RuntimeError("careless adapter that forgot to catch")
        return self._result


def _run(sources: list[FakeSource], profile: Profile | None = None) -> SearchResult:
    seeker = JobSeeker.default(list(sources), profile or _profile())
    return seeker.run(SearchQuery(max_age_days=None))


class TestFanOutAndCollect:
    def test_collects_jobs_from_every_source(self) -> None:
        a = FakeSource("a", SourceResult(source="a", jobs=[_job("Python Dev", source="a")]))
        b = FakeSource("b", SourceResult(source="b", jobs=[_job("RAG Dev", source="b")]))
        result = _run([a, b])
        assert {j.job.title for j in result.jobs} == {"Python Dev", "RAG Dev"}

    def test_no_sources_yields_an_empty_incomplete_result(self) -> None:
        result = _run([])
        assert result.jobs == []
        assert result.is_complete is False  # zero sources ran


class TestFailureIsolation:
    def test_a_raising_source_does_not_abort_the_run(self) -> None:
        """The never-raise contract is prose; the orchestrator must not trust it. A careless
        adapter that raises is caught and reported, and the healthy source still returns."""
        good = FakeSource(
            "good", SourceResult(source="good", jobs=[_job("Python Dev", source="good")])
        )
        bad = FakeSource("bad", raises=True)
        result = _run([good, bad])
        assert {j.job.title for j in result.jobs} == {"Python Dev"}
        coverage = {c.source: c for c in result.coverage}
        assert coverage["bad"].failed
        assert "careless adapter" in coverage["bad"].error
        assert result.is_complete is False

    def test_a_reported_source_error_is_carried_into_coverage(self) -> None:
        good = FakeSource("good", SourceResult(source="good", jobs=[_job("Dev", source="good")]))
        down = FakeSource("down", SourceResult(source="down", error="HTTP 503"))
        result = _run([good, down])
        assert {c.source: c.error for c in result.coverage}["down"] == "HTTP 503"


class TestCombination:
    def test_the_same_posting_from_two_boards_is_merged(self) -> None:
        a = FakeSource(
            "a", SourceResult(source="a", jobs=[_job("AI Engineer", source="a", url="https://a/1")])
        )
        b = FakeSource(
            "b", SourceResult(source="b", jobs=[_job("AI Engineer", source="b", url="https://b/2")])
        )
        result = _run([a, b])
        assert len(result.jobs) == 1  # one posting, two boards

    def test_ranks_by_fit_descending(self) -> None:
        high = _job("Python and RAG", source="a", description="python rag")  # 3 + 2
        low = _job("Python only", source="a", description="python")  # 3
        src = FakeSource("a", SourceResult(source="a", jobs=[low, high]))
        result = _run([src])
        assert [j.job.title for j in result.jobs] == ["Python and RAG", "Python only"]
        assert result.jobs[0].fit.value > result.jobs[1].fit.value


class TestEligibilityFiltering:
    def test_an_excluded_job_is_dropped(self) -> None:
        eligible = _job("Global Dev", source="a", hints=EligibilityHints(location_restrictions=()))
        excluded = _job(
            "US Dev", source="a", hints=EligibilityHints(location_restrictions=("United States",))
        )
        src = FakeSource("a", SourceResult(source="a", jobs=[eligible, excluded]))
        result = _run([src])
        assert {j.job.title for j in result.jobs} == {"Global Dev"}

    def test_unverified_is_kept_by_default(self) -> None:
        unknown = _job(
            "Mystery Dev", source="a", description="remote role", hints=EligibilityHints()
        )
        src = FakeSource("a", SourceResult(source="a", jobs=[unknown]))
        result = _run([src])
        assert len(result.jobs) == 1

    def test_unverified_is_dropped_when_the_profile_opts_out(self) -> None:
        unknown = _job(
            "Mystery Dev", source="a", description="remote role", hints=EligibilityHints()
        )
        src = FakeSource("a", SourceResult(source="a", jobs=[unknown]))
        result = _run([src], _profile(include_unverified=False))
        assert result.jobs == []


class TestAgeBackstop:
    def test_a_stale_job_from_a_source_that_ignored_max_age_is_dropped(self) -> None:
        """max_age_days is part of the query contract; the orchestrator enforces it centrally so a
        source that ignores it cannot leak stale postings into the ranked result."""
        from datetime import UTC, datetime, timedelta

        fresh = _job("Fresh Dev", source="a", posted_at=datetime.now(UTC) - timedelta(days=2))
        stale = _job("Stale Dev", source="a", posted_at=datetime.now(UTC) - timedelta(days=400))
        src = FakeSource("a", SourceResult(source="a", jobs=[fresh, stale]))
        seeker = JobSeeker.default([src], _profile())
        result = seeker.run(SearchQuery(max_age_days=30))
        assert {j.job.title for j in result.jobs} == {"Fresh Dev"}

    def test_an_undated_job_survives_the_age_filter(self) -> None:
        """No date means we cannot judge age; keep it rather than silently drop it."""
        undated = _job("Undated Dev", source="a", posted_at=None)
        src = FakeSource("a", SourceResult(source="a", jobs=[undated]))
        seeker = JobSeeker.default([src], _profile())
        result = seeker.run(SearchQuery(max_age_days=30))
        assert len(result.jobs) == 1


class TestCoverage:
    def test_reports_scanned_kept_and_truncated_per_source(self) -> None:
        jobs = [_job("Global Dev", source="a", hints=EligibilityHints(location_restrictions=()))]
        src = FakeSource("a", SourceResult(source="a", jobs=jobs, scanned=50, truncated=True))
        result = _run([src])
        cov = {c.source: c for c in result.coverage}["a"]
        assert cov.scanned == 50
        assert cov.kept == 1
        assert cov.truncated is True

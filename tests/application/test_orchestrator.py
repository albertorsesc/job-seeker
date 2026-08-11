"""Covers `job_seeker.application.orchestrator`.

The orchestrator is the combination spine: it fans sources out, merges the same posting seen on
several boards, scores and classifies each against the profile, drops what the seeker cannot hold,
and ranks the rest. Exercised with in-memory fake sources; no network.
"""

from __future__ import annotations

from job_seeker.application.orchestrator import JobSeeker
from job_seeker.domain.models import (
    EligibilityHints,
    EligibilityStatus,
    Job,
    SearchQuery,
    SearchResult,
    SortOrder,
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
        assert (result.all_sources_ran and result.fully_scanned) is False  # zero sources ran


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
        assert (result.all_sources_ran and result.fully_scanned) is False

    def test_a_reported_source_error_is_carried_into_coverage(self) -> None:
        good = FakeSource("good", SourceResult(source="good", jobs=[_job("Dev", source="good")]))
        down = FakeSource("down", SourceResult(source="down", error="HTTP 503"))
        result = _run([good, down])
        assert {c.source: c.error for c in result.coverage}["down"] == "HTTP 503"


class TestScanDepthAndMaxResultsAreDifferentThings:
    """One parameter used to mean both, and it silently meant the worse one.

    `--limit 5` reads as "give me five results". It was a per-board fetch depth applied before
    ranking, so it shrank the pool the answer was chosen from: the seeker got five of whatever was
    fetched first, and the best-fitting posting might never have been read at all.
    """

    @staticmethod
    def _board(name: str, count: int) -> FakeSource:
        """A board of engineers whose fit rises with the index, so rank and fetch order differ."""
        jobs = [
            _job(
                f"Engineer {index}",
                source=name,
                company=f"Co {name}{index}",
                description=" ".join(["python"] * (index + 1)),
            )
            for index in range(count)
        ]
        return FakeSource(name, SourceResult(source=name, jobs=jobs, scanned=count))

    def _search(self, sources: list[FakeSource], **query: object) -> SearchResult:
        seeker = JobSeeker.default(list(sources), _profile())
        return seeker.run(SearchQuery(terms=["engineer"], max_age_days=None, **query))  # type: ignore[arg-type]

    def test_max_results_caps_the_output_after_ranking(self) -> None:
        result = self._search([self._board("a", 10)], max_results=3)
        assert len(result.jobs) == 3
        ranks = [scored.fit.raw for scored in result.jobs]
        assert ranks == sorted(ranks, reverse=True)  # the three best, not the three fetched first

    def test_no_cap_returns_everything_ranked(self) -> None:
        assert len(self._search([self._board("a", 10)]).jobs) == 10

    def test_coverage_counts_what_matched_not_what_survived_the_cap(self) -> None:
        """So `sum(kept) > len(jobs)` is how a caller sees the cap bit. Counting post-cap would
        make a board that contributed ten matches look like it contributed three."""
        result = self._search([self._board("a", 10)], max_results=3)
        assert sum(c.kept for c in result.coverage) == 10
        assert len(result.jobs) == 3

    def test_a_deep_scan_with_a_small_cap_is_now_expressible(self) -> None:
        """The combination that actually answers "the five best jobs", and the one the old single
        parameter could not say: it could only fetch fewer, which returns five worse ones."""
        result = self._search(
            [self._board("a", 10), self._board("b", 10)], scan_depth_per_source=10, max_results=5
        )
        assert len(result.jobs) == 5
        assert sum(c.kept for c in result.coverage) == 20


class TestStatedOnlyAndConfidenceOrder:
    """Two questions a seeker asks that fit alone cannot answer.

    "What can I definitely hold" is not "what suits me best". A board that never said who may hold
    a role produces `remote-verify`, which is a lead, and live data returned a 26% lead above a 5%
    certainty. Which of those belongs first is the seeker's call, not the engine's.
    """

    @staticmethod
    def _board() -> FakeSource:
        jobs = [
            # A weak match the board explicitly opened to the seeker's own country.
            _job(
                "Engineer A",
                source="s",
                company="Certain Co",
                hints=EligibilityHints(location_restrictions=("Testland",)),
            ),
            # A strong match nobody cleared: no structured hints, and text that says nothing.
            _job(
                "Engineer B",
                source="s",
                company="Unverified Co",
                description="python rag " * 8,
                hints=EligibilityHints(),
            ),
        ]
        return FakeSource("s", SourceResult(source="s", jobs=jobs, scanned=2))

    def _run(self, **query: object) -> SearchResult:
        seeker = JobSeeker.default([self._board()], _profile())
        return seeker.run(SearchQuery(terms=["engineer"], max_age_days=None, **query))  # type: ignore[arg-type]

    def test_by_default_the_better_match_wins_even_if_unverified(self) -> None:
        result = self._run()
        assert [j.job.company for j in result.jobs] == ["Unverified Co", "Certain Co"]

    def test_confidence_order_puts_the_stated_verdict_first(self) -> None:
        result = self._run(sort=SortOrder.CONFIDENCE)
        assert [j.job.company for j in result.jobs] == ["Certain Co", "Unverified Co"]

    def test_confidence_order_still_uses_fit_inside_a_tier(self) -> None:
        """It groups, it does not reshuffle: two equally stated postings keep fit order.

        This is why the tiers are stated-vs-unstated and not one per status. Ranking home-based
        above regional put a 3% Node.js role above a 26% Senior AI Engineer on live data: the
        difference between those statuses is geography, not confidence.
        """
        jobs = [
            _job(
                "Engineer weak",
                source="s",
                company="Weak",
                hints=EligibilityHints(location_restrictions=("Testland",)),
            ),
            _job(
                "Engineer strong",
                source="s",
                company="Strong",
                description="python rag " * 8,
                hints=EligibilityHints(location_restrictions=("Testland",)),
            ),
        ]
        seeker = JobSeeker.default(
            [FakeSource("s", SourceResult(source="s", jobs=jobs))], _profile()
        )
        result = seeker.run(
            SearchQuery(terms=["engineer"], max_age_days=None, sort=SortOrder.CONFIDENCE)
        )
        assert [j.job.company for j in result.jobs] == ["Strong", "Weak"]

    def test_stated_only_drops_what_no_board_cleared(self) -> None:
        result = self._run(stated_only=True)
        assert [j.job.company for j in result.jobs] == ["Certain Co"]
        assert all(
            j.eligibility.status is not EligibilityStatus.REMOTE_UNVERIFIED for j in result.jobs
        )

    def test_stated_only_is_off_by_default(self) -> None:
        assert len(self._run().jobs) == 2

    def test_stated_only_narrows_and_never_widens(self) -> None:
        """The profile's `include_unverified: false` is a standing preference. A per-search flag
        must not be able to undo it, or a seeker who opted out gets leads back without asking."""
        opted_out = _profile()
        opted_out = opted_out.model_copy(
            update={
                "eligibility": opted_out.eligibility.model_copy(
                    update={"include_unverified": False}
                )
            }
        )
        seeker = JobSeeker.default([self._board()], opted_out)
        result = seeker.run(SearchQuery(terms=["engineer"], max_age_days=None, stated_only=False))
        assert [j.job.company for j in result.jobs] == ["Certain Co"]


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


class TestMinimumFit:
    """Ranking alone stopped being enough once the engine read deeply: a full-depth run leaves a
    list whose median fit is a few percent, and the ranking is what hides that."""

    @staticmethod
    def _run(**query: object) -> SearchResult:
        board = FakeSource(
            "b",
            SourceResult(
                source="b",
                jobs=[
                    _job("Warehouse Engineer", source="b", description="forklift"),
                    _job("Python Engineer", source="b", description="python and rag"),
                ],
                scanned=2,
            ),
        )
        seeker = JobSeeker.default([board], _profile())
        return seeker.run(SearchQuery(terms=["engineer"], max_age_days=None, **query))  # type: ignore[arg-type]

    def test_the_default_keeps_a_posting_that_matched_nothing(self) -> None:
        """0.0 must mean no fit filtering, never drop everything."""
        titles = [scored.job.title for scored in self._run().jobs]
        assert "Warehouse Engineer" in titles

    def test_a_posting_below_the_floor_is_dropped(self) -> None:
        titles = [scored.job.title for scored in self._run(min_fit=0.5).jobs]
        assert titles == ["Python Engineer"]

    def test_a_posting_exactly_at_the_floor_is_kept(self) -> None:
        """At, not above: a caller who sets the floor to a value they just saw must still see it."""
        exact = next(s for s in self._run().jobs if s.job.title == "Python Engineer").fit.value
        assert [s.job.title for s in self._run(min_fit=exact).jobs] == ["Python Engineer"]

    def test_it_narrows_without_reordering(self) -> None:
        unfiltered = [s.job.title for s in self._run().jobs if s.fit.value >= 0.4]
        assert [s.job.title for s in self._run(min_fit=0.4).jobs] == unfiltered

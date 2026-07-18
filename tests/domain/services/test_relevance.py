"""Covers `job_seeker.domain.services.relevance`.

Eligibility answers "can I hold this job". Relevance answers "is this the job I searched for". A
run that filtered only on eligibility returned every holdable posting, so a search for "AI Engineer"
surfaced an Aluminum Sourcing Director. This stage narrows to what the seeker actually wants,
driven by the profile's role rules and the query's terms.
"""

from __future__ import annotations

from collections.abc import Callable

from job_seeker.domain.models import Job
from job_seeker.domain.profile import Profile
from job_seeker.domain.services.relevance import RelevanceFilter


def _relevant(job: Job, terms: list[str], profile: Profile | None = None) -> bool:
    return RelevanceFilter(profile or Profile()).assess(job, terms).keep


class TestWantedSignals:
    def test_a_title_matching_a_search_term_is_relevant(self, make_job: Callable[..., Job]) -> None:
        assert _relevant(make_job(title="Senior AI Engineer"), ["AI Engineer"])

    def test_a_title_missing_every_wanted_word_is_dropped(
        self, make_job: Callable[..., Job]
    ) -> None:
        assert not _relevant(make_job(title="Aluminum Sourcing Director"), ["AI Engineer"])

    def test_a_wanted_word_matches_whole_words_only(self, make_job: Callable[..., Job]) -> None:
        """ "ai" must not fire on "captain" or "email"."""
        assert not _relevant(make_job(title="Retail Captain, Email Team"), ["AI"])

    def test_role_include_from_the_profile_also_marks_a_job_wanted(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(role_include=["engineer", "developer"])
        assert _relevant(make_job(title="Backend Developer"), [], profile)

    def test_with_no_terms_and_no_role_include_everything_is_relevant(
        self, make_job: Callable[..., Job]
    ) -> None:
        """Nothing to narrow by means the seeker gets everything eligible, not nothing."""
        assert _relevant(make_job(title="Anything At All"), [])


class TestExclusions:
    def test_a_role_exclude_word_drops_the_job_even_if_it_is_wanted(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(role_exclude=["manager"])
        assert not _relevant(make_job(title="Engineering Manager"), ["engineer"], profile)

    def test_a_false_positive_phrase_drops_the_job(self, make_job: Callable[..., Job]) -> None:
        """An "AI" seeker must not get human "agent" roles like a booking agent."""
        profile = Profile(false_positive_terms=["booking agent"])
        job = make_job(title="Booking Agent", description="Handle travel bookings.")
        assert not _relevant(job, ["agent"], profile)

    def test_exclusion_wins_over_a_matching_term(self, make_job: Callable[..., Job]) -> None:
        profile = Profile(role_exclude=["sales"])
        assert not _relevant(make_job(title="Sales Engineer"), ["engineer"], profile)


class TestAssessAll:
    def test_pairs_every_job_with_a_verdict_in_order(self, make_job: Callable[..., Job]) -> None:
        """Every job comes back, kept or not, so a caller keeps the on-topic ones without losing
        the reason the others were dropped."""
        jobs = [
            make_job(title="AI Engineer", url="https://a/1"),
            make_job(title="Aluminum Director", url="https://a/2"),
            make_job(title="ML Engineer", url="https://a/3"),
        ]
        assessed = RelevanceFilter(Profile()).assess_all(jobs, ["engineer"])
        assert [(j.title, r.keep) for j, r in assessed] == [
            ("AI Engineer", True),
            ("Aluminum Director", False),
            ("ML Engineer", True),
        ]


class TestReasonIsRecorded:
    """The point of the stage recording its verdict: "why is this job here / gone?" is answerable."""

    def test_a_kept_job_names_the_term_it_matched(self, make_job: Callable[..., Job]) -> None:
        verdict = RelevanceFilter(Profile()).assess(make_job(title="Senior Engineer"), ["engineer"])
        assert verdict.keep
        assert "engineer" in verdict.reason

    def test_a_dropped_off_topic_job_says_so(self, make_job: Callable[..., Job]) -> None:
        verdict = RelevanceFilter(Profile()).assess(
            make_job(title="Aluminum Director"), ["engineer"]
        )
        assert not verdict.keep
        assert verdict.reason == "title matches no search term"

    def test_an_excluded_job_names_the_excluding_term(self, make_job: Callable[..., Job]) -> None:
        profile = Profile(role_exclude=["sales"])
        verdict = RelevanceFilter(profile).assess(make_job(title="Sales Engineer"), ["engineer"])
        assert not verdict.keep
        assert "sales" in verdict.reason

    def test_a_false_positive_job_says_it_names_a_human_role(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(false_positive_terms=["booking agent"])
        job = make_job(title="Booking Agent", description="Handle travel bookings.")
        verdict = RelevanceFilter(profile).assess(job, ["agent"])
        assert not verdict.keep
        assert "booking agent" in verdict.reason

    def test_with_no_terms_the_reason_says_nothing_to_narrow_by(
        self, make_job: Callable[..., Job]
    ) -> None:
        verdict = RelevanceFilter(Profile()).assess(make_job(title="Anything"), [])
        assert verdict.keep
        assert verdict.reason == "no search terms set"

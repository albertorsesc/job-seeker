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
    return RelevanceFilter(profile or Profile()).is_relevant(job, terms)


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


class TestFilterAll:
    def test_keeps_only_the_relevant_jobs_in_order(self, make_job: Callable[..., Job]) -> None:
        jobs = [
            make_job(title="AI Engineer", url="https://a/1"),
            make_job(title="Aluminum Director", url="https://a/2"),
            make_job(title="ML Engineer", url="https://a/3"),
        ]
        kept = RelevanceFilter(Profile()).filter(jobs, ["engineer"])
        assert [j.title for j in kept] == ["AI Engineer", "ML Engineer"]

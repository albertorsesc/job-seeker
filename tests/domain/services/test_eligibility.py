"""Covers `job_seeker.domain.services.eligibility`.

The classifier is the product's heart: it decides whether the seeker can actually hold a role.
Two paths, both driven entirely by the profile. When a board reports structured restrictions
(EligibilityHints), decide from them; when it reports nothing, fall back to reading the posting
text against the profile's term lists. Every status and both paths are pinned here.
"""

from __future__ import annotations

from collections.abc import Callable

from job_seeker.domain.models import EligibilityHints, EligibilityStatus, Job
from job_seeker.domain.profile import EligibilityRules, LocationProfile, Profile
from job_seeker.domain.services.eligibility import EligibilityClassifier

S = EligibilityStatus


def _profile() -> Profile:
    return Profile(
        location=LocationProfile(country="Testland", timezone_utc_offset=-6.0),
        eligibility=EligibilityRules(
            eligible_regions=["testland", "latam"],
            disqualifying_authorization_terms=["us citizen", "security clearance"],
            location_lock_terms=["us only", "eu only"],
            max_timezone_distance_hours=2.0,
        ),
    )


def _classify(job: Job) -> EligibilityStatus:
    return EligibilityClassifier(_profile()).classify(job).status


class TestStructuredLocation:
    def test_no_location_restriction_is_global(self, make_job: Callable[..., Job]) -> None:
        job = make_job(hints=EligibilityHints(location_restrictions=(), timezone_restrictions=()))
        assert _classify(job) == S.GLOBAL

    def test_a_restriction_to_the_home_country_is_home_based(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = make_job(hints=EligibilityHints(location_restrictions=("Testland",)))
        assert _classify(job) == S.HOME_BASED

    def test_a_restriction_to_an_eligible_region_is_regional(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = make_job(hints=EligibilityHints(location_restrictions=("Latam", "Brazil")))
        assert _classify(job) == S.REGIONAL

    def test_a_restriction_that_excludes_the_seeker_is_excluded_location(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = make_job(hints=EligibilityHints(location_restrictions=("United States", "Canada")))
        assert _classify(job) == S.EXCLUDED_LOCATION


class TestStructuredTimezone:
    def test_a_timezone_within_range_is_allowed(self, make_job: Callable[..., Job]) -> None:
        # seeker at -6, max distance 2 -> -8..-4 acceptable
        job = make_job(
            hints=EligibilityHints(location_restrictions=(), timezone_restrictions=(-5.0, -6.0))
        )
        assert _classify(job) == S.GLOBAL

    def test_a_timezone_lock_out_of_range_is_excluded_timezone(
        self, make_job: Callable[..., Job]
    ) -> None:
        # seeker at -6, max distance 2; a CET (+1) lock is 7 hours away
        job = make_job(
            hints=EligibilityHints(location_restrictions=(), timezone_restrictions=(1.0, 2.0))
        )
        assert _classify(job) == S.EXCLUDED_TIMEZONE

    def test_timezone_never_excludes_when_no_max_distance_is_set(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(
            location=LocationProfile(country="Testland", timezone_utc_offset=-6.0),
            eligibility=EligibilityRules(max_timezone_distance_hours=None),
        )
        job = make_job(
            hints=EligibilityHints(location_restrictions=(), timezone_restrictions=(1.0,))
        )
        assert EligibilityClassifier(profile).classify(job).status == S.GLOBAL


class TestTextFallback:
    """A board that reports no structured data (hints all None): read the posting text."""

    def _text_job(self, make_job: Callable[..., Job], text: str) -> Job:
        return make_job(description=text, hints=EligibilityHints())

    def test_an_authorization_demand_is_excluded_authorization(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = self._text_job(make_job, "Must be a US citizen with active clearance.")
        assert _classify(job) == S.EXCLUDED_AUTHORIZATION

    def test_a_location_lock_without_an_eligible_region_is_excluded_location(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = self._text_job(make_job, "This role is US only.")
        assert _classify(job) == S.EXCLUDED_LOCATION

    def test_a_worldwide_mention_is_global(self, make_job: Callable[..., Job]) -> None:
        job = self._text_job(make_job, "Fully remote, hire from anywhere worldwide.")
        assert _classify(job) == S.GLOBAL

    def test_a_home_country_mention_is_home_based(self, make_job: Callable[..., Job]) -> None:
        job = self._text_job(make_job, "Remote within Testland only.")
        assert _classify(job) == S.HOME_BASED

    def test_an_eligible_region_mention_is_regional(self, make_job: Callable[..., Job]) -> None:
        job = self._text_job(make_job, "Open to candidates across LATAM.")
        assert _classify(job) == S.REGIONAL

    def test_no_signal_at_all_is_remote_unverified(self, make_job: Callable[..., Job]) -> None:
        job = self._text_job(make_job, "A great remote engineering role. Apply now.")
        assert _classify(job) == S.REMOTE_UNVERIFIED

    def test_a_lock_term_with_an_eligible_region_present_is_not_auto_excluded(
        self, make_job: Callable[..., Job]
    ) -> None:
        """ "US only" but also "LATAM welcome" should not hard-exclude on the lock alone."""
        job = self._text_job(make_job, "US only for HQ roles, but LATAM welcome for this one.")
        assert _classify(job) != S.EXCLUDED_LOCATION


class TestMatchingIsWordAccurateNotSubstring:
    """Substring matching produced false verdicts on the product's core promise."""

    def test_a_us_state_containing_the_home_country_name_is_not_home_based(
        self, make_job: Callable[..., Job]
    ) -> None:
        """ "New Mexico" contains "Mexico" but is a US state. A Mexico seeker cannot hold a job
        restricted there, and calling it home-based is a false "you can hold this"."""
        job = make_job(hints=EligibilityHints(location_restrictions=("New Mexico",)))
        assert _classify(job) == S.EXCLUDED_LOCATION

    def test_a_short_region_token_does_not_match_inside_a_word(
        self, make_job: Callable[..., Job]
    ) -> None:
        """A profile region like "us" must not match "houston" or "business" in a description."""
        profile = Profile(
            location=LocationProfile(country="Nowhere"),
            eligibility=EligibilityRules(eligible_regions=["us"]),
        )
        job = make_job(
            description="A great role for our Houston business team.", hints=EligibilityHints()
        )
        assert EligibilityClassifier(profile).classify(job).status != S.REGIONAL

    def test_a_global_word_in_marketing_copy_does_not_make_a_us_only_job_global(
        self, make_job: Callable[..., Job]
    ) -> None:
        """ "global SaaS company" is filler, not a hire-from-anywhere claim."""
        job = make_job(
            description="Remote within the United States. We are a global SaaS company.",
            hints=EligibilityHints(),
        )
        assert _classify(job) != S.GLOBAL

    def test_a_strong_global_phrase_still_reads_global(self, make_job: Callable[..., Job]) -> None:
        job = make_job(description="Fully remote, hire from anywhere.", hints=EligibilityHints())
        assert _classify(job) == S.GLOBAL

    def test_a_worldwide_restriction_string_is_global_not_excluded(
        self, make_job: Callable[..., Job]
    ) -> None:
        """A board that encodes openness as the word "Worldwide" (not an empty list) must read
        GLOBAL, not be hard-excluded for a profile that does not list "worldwide" as a region."""
        profile = Profile(
            location=LocationProfile(country="Portugal"),
            eligibility=EligibilityRules(eligible_regions=["portugal", "europe"]),
        )
        job = make_job(hints=EligibilityHints(location_restrictions=("Worldwide",)))
        assert EligibilityClassifier(profile).classify(job).status == S.GLOBAL


class TestRegionMapping:
    """A profile's broad region must accept a board's specific-country restriction, and back."""

    def test_a_latam_seeker_is_regional_for_a_brazil_restricted_job(
        self, make_job: Callable[..., Job]
    ) -> None:
        """The card-080 fix: "Brazil" is in LATAM, so a LATAM seeker can hold it. Exact matching
        wrongly excluded this."""
        profile = Profile(
            location=LocationProfile(country="Mexico", timezone_utc_offset=-6.0),
            eligibility=EligibilityRules(eligible_regions=["latam"]),
        )
        job = make_job(hints=EligibilityHints(location_restrictions=("Brazil",)))
        assert EligibilityClassifier(profile).classify(job).status == S.REGIONAL

    def test_a_portugal_seeker_is_regional_for_a_europe_restricted_job(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(
            location=LocationProfile(country="Portugal"),
            eligibility=EligibilityRules(eligible_regions=["portugal"]),
        )
        job = make_job(hints=EligibilityHints(location_restrictions=("Europe",)))
        assert EligibilityClassifier(profile).classify(job).status == S.REGIONAL

    def test_a_latam_seeker_is_still_excluded_from_a_us_only_job(
        self, make_job: Callable[..., Job]
    ) -> None:
        """The map must not over-include: the US is not LATAM."""
        profile = Profile(
            location=LocationProfile(country="Mexico"),
            eligibility=EligibilityRules(eligible_regions=["latam"]),
        )
        job = make_job(hints=EligibilityHints(location_restrictions=("United States",)))
        assert EligibilityClassifier(profile).classify(job).status == S.EXCLUDED_LOCATION

    def test_a_region_member_country_is_recognized_in_the_text_path_too(
        self, make_job: Callable[..., Job]
    ) -> None:
        profile = Profile(
            location=LocationProfile(country="Mexico"),
            eligibility=EligibilityRules(eligible_regions=["latam"]),
        )
        job = make_job(description="Open to candidates in Argentina.", hints=EligibilityHints())
        assert EligibilityClassifier(profile).classify(job).status == S.REGIONAL


class TestReasonIsPopulated:
    def test_every_verdict_carries_a_reason(self, make_job: Callable[..., Job]) -> None:
        """The reason is the product: "why can't I hold this?" must always have an answer."""
        for hints, text in [
            (EligibilityHints(location_restrictions=("United States",)), ""),
            (EligibilityHints(), "US only"),
            (EligibilityHints(location_restrictions=()), ""),
        ]:
            verdict = EligibilityClassifier(_profile()).classify(
                make_job(description=text, hints=hints)
            )
            assert verdict.reason, f"no reason for status {verdict.status}"

"""Covers `job_seeker.domain.profile`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_seeker.domain.profile import EligibilityRules, Profile


class TestProfileShipsNoCandidateData:
    """The engine's whole premise: swap the profile, keep the code.

    A default that encodes one person's role, region or vocabulary breaks that premise
    silently, because it still produces plausible-looking results for the wrong person. This
    test enumerates EVERY field that could carry such a default, so the guarantee cannot be
    quietly stepped over by adding one more.
    """

    def test_no_field_defaults_to_candidate_specific_data(self) -> None:
        blank = Profile()
        assert blank.name == "Anonymous"
        assert blank.headline == ""
        assert blank.seniority == ""
        assert blank.skills == {}
        assert blank.search_terms == []
        assert blank.role_include == []
        assert blank.role_exclude == []
        assert blank.false_positive_terms == []

    def test_no_eligibility_rule_defaults_to_a_place_or_a_country_specific_term(self) -> None:
        """Every eligibility rule is data supplied by the profile, empty meaning "off". No rule
        may bake in a country: `exclude_us_only` did exactly that, in an engine that claims to
        serve a seeker anywhere."""
        rules = Profile().eligibility
        assert rules.eligible_regions == []
        assert rules.disqualifying_authorization_terms == []
        assert rules.location_lock_terms == []
        assert rules.max_timezone_distance_hours is None

    def test_eligible_regions_never_defaults_to_a_work_mode(self) -> None:
        """ "remote" is not a region. It appears in nearly every posting a remote-jobs engine
        ingests, so allowing it as a region would pass everything through the filter."""
        assert "remote" not in Profile().eligibility.eligible_regions


class TestEligibilityRulesAreData:
    """Every rule is a value the profile supplies, not a switch wired to a hidden term list.

    The old shape had `exclude_us_only: bool`, which could only mean "run a US-specific term
    list compiled into the classifier". That is the profile-driven principle failing in the one
    module that is the product.
    """

    def test_empty_means_the_rule_is_off(self) -> None:
        """A blank ruleset excludes nothing: the seeker has stated no constraints, so the engine
        invents none. Empty is never "match everything" and never "match nothing"; it is "this
        rule does not apply"."""
        blank = EligibilityRules()
        assert blank.eligible_regions == []
        assert blank.disqualifying_authorization_terms == []
        assert blank.location_lock_terms == []
        assert blank.max_timezone_distance_hours is None

    def test_carries_authorization_terms_the_seeker_cannot_meet(self) -> None:
        rules = EligibilityRules(
            disqualifying_authorization_terms=["us citizen", "security clearance"]
        )
        assert "security clearance" in rules.disqualifying_authorization_terms

    def test_carries_location_lock_terms(self) -> None:
        rules = EligibilityRules(location_lock_terms=["us only", "eu only"])
        assert "eu only" in rules.location_lock_terms

    def test_timezone_distance_is_one_number_not_an_offset_list(self) -> None:
        """`max_timezone_distance_hours` is measured against `location.timezone_utc_offset`, so
        it cannot drift from the seeker's own timezone the way a hand-listed set of offsets did.
        None means timezone never excludes."""
        assert EligibilityRules(max_timezone_distance_hours=2.0).max_timezone_distance_hours == 2.0
        assert EligibilityRules().max_timezone_distance_hours is None

    def test_zero_timezone_distance_is_valid(self) -> None:
        """A legitimately strict setting: only postings at the seeker's exact offset."""
        assert EligibilityRules(max_timezone_distance_hours=0).max_timezone_distance_hours == 0

    def test_a_negative_timezone_distance_is_rejected(self) -> None:
        """A negative bound excludes every posting on timezone, a silent "match nothing" that a
        user typo in the YAML would introduce. Reject it at the boundary rather than let the
        classifier quietly return empty."""
        with pytest.raises(ValidationError):
            EligibilityRules(max_timezone_distance_hours=-1.0)

    def test_terms_are_lower_cased_and_stripped_at_the_boundary(self) -> None:
        """They are matched against lower-cased posting text, so a term authored "US Only" could
        never match. Normalizing here turns a silent no-match into a term that works."""
        rules = EligibilityRules(
            eligible_regions=["  Portugal ", "EMEA"],
            location_lock_terms=["US Only"],
            disqualifying_authorization_terms=["Security Clearance"],
        )
        assert rules.eligible_regions == ["portugal", "emea"]
        assert rules.location_lock_terms == ["us only"]
        assert rules.disqualifying_authorization_terms == ["security clearance"]

    def test_blank_terms_are_dropped_so_they_cannot_match_everything(self) -> None:
        """An empty string is a substring of every posting, so one stray blank line in the YAML
        would match all of them. Drop blanks rather than let a rule silently pass everything."""
        assert EligibilityRules(location_lock_terms=["", "  ", "us only"]).location_lock_terms == [
            "us only"
        ]

    def test_unverified_postings_are_included_by_default(self) -> None:
        """Recall over precision, but configurable. A missed real job costs the seeker more than
        reading one posting whose eligibility could not be confirmed. A seeker who wants only
        confirmed-eligible roles sets this false."""
        assert EligibilityRules().include_unverified is True
        assert EligibilityRules(include_unverified=False).include_unverified is False


class TestProfileConstruction:
    def test_is_constructible_with_no_arguments(self) -> None:
        """The loader builds a Profile before it validates one, so a bare one must not raise."""
        assert Profile().name == "Anonymous"

    def test_carries_the_values_it_is_given(self, profile: Profile) -> None:
        assert profile.location.country == "Testland"
        assert profile.location.timezone_utc_offset == -6.0
        assert profile.skills
        assert profile.eligibility.disqualifying_authorization_terms

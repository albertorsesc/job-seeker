"""Covers `job_seeker.domain.profile`."""

from __future__ import annotations

from job_seeker.domain.profile import Profile


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

    def test_no_eligibility_rule_defaults_to_a_place(self) -> None:
        blank = Profile()
        assert blank.location.country == "Worldwide"
        assert blank.eligibility.eligible_regions == []
        assert blank.eligibility.acceptable_timezone_offsets == []

    def test_eligible_regions_never_defaults_to_a_work_mode(self) -> None:
        """ "remote" is not a region. It appears in nearly every posting a remote-jobs engine
        ingests, so allowing it as a region would pass everything through the filter."""
        assert "remote" not in Profile().eligibility.eligible_regions


class TestProfileConstruction:
    def test_is_constructible_with_no_arguments(self) -> None:
        """The loader builds a Profile before it validates one, so a bare one must not raise."""
        assert Profile().name == "Anonymous"

    def test_carries_the_values_it_is_given(self, profile: Profile) -> None:
        assert profile.location.country == "Testland"
        assert profile.location.timezone_utc_offset == -6.0
        assert profile.skills
        assert profile.eligibility.exclude_us_only is True

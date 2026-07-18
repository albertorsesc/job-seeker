"""Covers `job_seeker.domain.regions`."""

from __future__ import annotations

from job_seeker.domain.regions import expand_place


class TestExpandPlace:
    def test_a_region_expands_to_itself_plus_its_member_countries(self) -> None:
        places = expand_place("latam")
        assert "latam" in places
        assert "brazil" in places
        assert "mexico" in places
        assert "united states" not in places  # the US is not LATAM

    def test_a_region_alias_shares_the_same_member_countries(self) -> None:
        """ "latin america" and "latam" differ only in their own names; the countries are identical."""
        assert expand_place("latin america") ^ expand_place("latam") == {"latin america", "latam"}

    def test_a_plain_country_expands_to_just_itself(self) -> None:
        assert expand_place("brazil") == {"brazil"}

    def test_an_unknown_place_expands_to_just_itself(self) -> None:
        assert expand_place("atlantis") == {"atlantis"}

    def test_north_america_and_latam_are_distinct(self) -> None:
        assert "united states" in expand_place("north america")
        assert "united states" not in expand_place("latam")

    def test_europe_contains_its_countries(self) -> None:
        europe = expand_place("europe")
        assert "portugal" in europe
        assert "germany" in europe


class TestOverlapIsWhatEligibilityNeeds:
    def test_a_latam_profile_accepts_a_brazil_restriction(self) -> None:
        """The whole point: a broad profile region must intersect a specific country restriction."""
        profile_accepts = expand_place("latam")
        job_restricted_to = expand_place("brazil")
        assert profile_accepts & job_restricted_to

    def test_a_portugal_profile_accepts_a_europe_restriction(self) -> None:
        """And symmetrically: a specific home country must intersect a broad restriction."""
        profile_accepts = expand_place("portugal")
        job_restricted_to = expand_place("europe")
        assert profile_accepts & job_restricted_to

    def test_a_latam_profile_does_not_accept_a_us_restriction(self) -> None:
        assert not (expand_place("latam") & expand_place("united states"))

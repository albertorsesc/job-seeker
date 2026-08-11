"""Covers `job_seeker.domain.regions`."""

from __future__ import annotations

import pytest

from job_seeker.domain import regions
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


class TestOneCountryUnderItsSeveralNames:
    """Boards name countries in whatever vocabulary they store. WeWorkRemotely emits the ISO 3166
    official names, so a US-only posting arrives as "United States of America" where the profile,
    and every other board, says "United States". Without a canonical form those are two countries,
    and a seeker in one of them is told they cannot hold a job at home."""

    @pytest.mark.parametrize(
        "board_name,common_name",
        [
            ("united states of america", "united states"),
            ("usa", "united states"),
            ("united kingdom of great britain and northern ireland", "united kingdom"),
            ("uk", "united kingdom"),
            ("korea (republic of)", "south korea"),
            ("bolivia (plurinational state of)", "bolivia"),
            ("viet nam", "vietnam"),
            ("czech republic", "czechia"),
            ("uae", "united arab emirates"),
        ],
    )
    def test_a_variant_spelling_expands_to_the_same_country(
        self, board_name: str, common_name: str
    ) -> None:
        assert regions.expand_place(board_name) == regions.expand_place(common_name)

    def test_a_us_territory_carries_us_work_authorization(self) -> None:
        """Puerto Rico is geographically LATAM and legally the United States. This map exists to
        answer "may the seeker hold this role", so it follows the authorization: a seeker who can
        work in the US can take a Puerto Rico posting, and a LATAM seeker cannot."""
        assert regions.expand_place("puerto rico") == regions.expand_place("united states")

    def test_every_region_member_is_already_canonical(self) -> None:
        """Otherwise a region expands to a name that never matches a canonicalized restriction,
        and the alias table would have to be applied twice to be right once."""
        members = {m for members in regions.REGION_MEMBERS.values() for m in members}
        assert {m for m in members if regions.canonical_place(m) != m} == set()

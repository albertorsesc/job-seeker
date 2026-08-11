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


class TestOneNormalizationEntryPoint:
    """The bug this shape exists to prevent: normalizing and aliasing were two steps done by two
    modules in two orders, so the alias keys carrying punctuation were looked up in a string that
    had already had its punctuation stripped, and could never match."""

    @pytest.mark.parametrize(
        "board_name,common_name",
        [
            ("Korea (Republic of)", "south korea"),
            ("Korea, Republic of", "south korea"),
            ("Bolivia (Plurinational State of)", "bolivia"),
            ("Moldova (Republic of)", "moldova"),
        ],
    )
    def test_a_name_carrying_punctuation_still_reaches_its_alias(
        self, board_name: str, common_name: str
    ) -> None:
        assert regions.canonical_place(board_name) == common_name

    def test_every_alias_key_survives_normalization_unchanged(self) -> None:
        """Enforces the storage rule. A key that normalizes to something else is dead weight that
        nothing would ever look up, and it fails silently."""
        keys = set(regions._SPELLINGS) | set(regions._AUTHORIZATION_TERRITORIES)
        assert {key for key in keys if regions.normalize_place(key) != key} == set()

    @pytest.mark.parametrize(
        "written,canonical",
        [("México", "mexico"), ("PERÚ", "peru"), ("Curaçao", "curacao"), ("Panamá", "panama")],
    )
    def test_an_accented_place_is_the_same_place(self, written: str, canonical: str) -> None:
        """A seeker in Mexico writes their own country the way it is spelled there, and a board
        writes it without the mark. Held apart, a seeker's country stops matching itself."""
        assert regions.canonical_place(written) == canonical

    def test_canonicalizing_twice_changes_nothing(self) -> None:
        once = regions.canonical_place("United States of America")
        assert regions.canonical_place(once) == once


class TestSpellingsOf:
    def test_it_returns_every_name_for_the_place(self) -> None:
        assert regions.spellings_of("usa") == frozenset(
            {"usa", "united states", "united states of america"}
        )

    def test_it_answers_the_same_however_the_caller_spelled_it(self) -> None:
        assert regions.spellings_of("USA") == regions.spellings_of("United States of America")

    def test_an_unknown_place_is_its_own_only_spelling(self) -> None:
        assert regions.spellings_of("Narnia") == frozenset({"narnia"})

    def test_a_territory_does_not_lend_its_name_to_the_country_it_follows(self) -> None:
        """Prose naming the United States is not naming Puerto Rico. The structured path can make
        that inference because a restriction field is a precise claim; free text is not."""
        assert "puerto rico" not in regions.spellings_of("united states")

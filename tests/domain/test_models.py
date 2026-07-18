"""Covers `job_seeker.domain.models`."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from job_seeker.domain.models import (
    ELIGIBLE_STATUSES,
    Eligibility,
    EligibilityHints,
    EligibilityStatus,
    FitScore,
    Job,
    Relevance,
    ScoredJob,
)


class TestEligibilityHints:
    """What a board published about who may hold a role.

    Three states, and conflating any two of them is the failure this class exists to prevent:
    `None` (the board said nothing, so fall back to reading the text), `[]` (the board said
    explicitly there is no restriction), and a populated list (restricted to these). The old
    two-fields-on-Job shape defaulted to `[]`, so a board that simply has no such field looked
    identical to a board declaring the role open to everyone, and every posting from the four
    boards without structured data was silently promoted to unrestricted.
    """

    def test_a_board_that_says_nothing_is_the_default(self) -> None:
        hints = EligibilityHints()
        assert hints.location_restrictions is None
        assert hints.timezone_restrictions is None

    def test_none_is_distinct_from_empty(self) -> None:
        """The load-bearing distinction: unknown is not the same as unrestricted."""
        silent = EligibilityHints()
        unrestricted = EligibilityHints(location_restrictions=(), timezone_restrictions=())
        assert silent.location_restrictions is None
        assert unrestricted.location_restrictions == ()
        assert silent != unrestricted

    def test_carries_a_stated_location_restriction(self) -> None:
        hints = EligibilityHints(location_restrictions=("united states", "canada"))
        assert hints.location_restrictions == ("united states", "canada")

    def test_carries_stated_timezone_restrictions(self) -> None:
        hints = EligibilityHints(timezone_restrictions=(-5.0, -6.0))
        assert hints.timezone_restrictions == (-5.0, -6.0)

    def test_the_three_states_survive_a_json_round_trip(self) -> None:
        """The states are read across an MCP boundary, so they must survive serialization, not
        only in-process equality. This guards against a later `model_config` change (e.g.
        exclude_none to trim payloads) silently collapsing `None` to absent on the wire."""
        for hints in (
            EligibilityHints(),
            EligibilityHints(location_restrictions=(), timezone_restrictions=()),
            EligibilityHints(
                location_restrictions=("united states",), timezone_restrictions=(-6.0,)
            ),
        ):
            assert EligibilityHints.model_validate_json(hints.model_dump_json()) == hints
        assert EligibilityHints().model_dump()["location_restrictions"] is None
        assert (
            EligibilityHints(location_restrictions=()).model_dump()["location_restrictions"] == ()
        )

    def test_is_genuinely_immutable_not_only_unrebindable(self) -> None:
        """`frozen=True` alone blocks rebinding but not `restrictions.append(...)`, and leaves the
        model unhashable. Tuple members close both holes, so this asserts all three."""
        hints = EligibilityHints(location_restrictions=("united states",))
        with pytest.raises(ValidationError):
            hints.location_restrictions = ()  # rebinding the field is blocked
        assert not hasattr(hints.location_restrictions, "append")  # the value cannot be mutated
        assert hash(hints)  # and the value object is hashable


class TestEligibilityStatusRendering:
    """A reporter interpolates a status straight into its output.

    A plain `(str, Enum)` keeps `Enum.__str__`, so `f"{status}"` renders
    "EligibilityStatus.HOME_BASED" rather than the wire value. JSON hides it, because a str
    subclass serializes by value, so only the human-facing formats break. Pin every path a
    reporter can take.
    """

    @pytest.mark.parametrize("status", list(EligibilityStatus))
    def test_str_renders_wire_value(self, status: EligibilityStatus) -> None:
        assert str(status) == status.value

    @pytest.mark.parametrize("status", list(EligibilityStatus))
    def test_fstring_renders_wire_value(self, status: EligibilityStatus) -> None:
        assert f"{status}" == status.value

    def test_percent_format_renders_wire_value(self) -> None:
        # noqa UP031: the %-format is the subject under test, not a style choice. A reporter
        # or log line may use it, and it routes through __str__, so it must render the value.
        assert "%s" % EligibilityStatus.HOME_BASED == "home-based"  # noqa: UP031

    def test_json_renders_wire_value(self) -> None:
        assert json.dumps({"status": EligibilityStatus.GLOBAL}) == '{"status": "global"}'

    def test_still_compares_equal_to_its_string(self) -> None:
        """Guards the regression where someone swaps StrEnum for a plain Enum: every
        `status == "global"` in the codebase would silently become False rather than error.

        The ignore is mypy being wrong, not the code. strict_equality narrows both sides to
        literal types and reports them as non-overlapping, but a StrEnum member *is* a str:
        at runtime this is True, isinstance(member, str) is True, and it hashes as its value.
        """
        assert EligibilityStatus.GLOBAL == "global"  # type: ignore[comparison-overlap]


class TestEligibleStatuses:
    def test_holdable_statuses_are_eligible(self) -> None:
        for status in (
            EligibilityStatus.HOME_BASED,
            EligibilityStatus.REGIONAL,
            EligibilityStatus.GLOBAL,
            EligibilityStatus.REMOTE_UNVERIFIED,
        ):
            assert Eligibility(status=status, reason="test").is_eligible

    def test_excluded_statuses_are_not_eligible(self) -> None:
        for status in (
            EligibilityStatus.EXCLUDED_LOCATION,
            EligibilityStatus.EXCLUDED_TIMEZONE,
            EligibilityStatus.EXCLUDED_AUTHORIZATION,
        ):
            assert not Eligibility(status=status, reason="test").is_eligible

    def test_every_status_is_deliberately_classified(self) -> None:
        """Adding a status must be a decision, not an accidental exclusion."""
        assert set(EligibilityStatus) - ELIGIBLE_STATUSES == {
            EligibilityStatus.EXCLUDED_LOCATION,
            EligibilityStatus.EXCLUDED_TIMEZONE,
            EligibilityStatus.EXCLUDED_AUTHORIZATION,
        }


class TestJobCarriesHints:
    def test_a_job_reports_nothing_by_default(self, make_job: Callable[..., Job]) -> None:
        """A board that provides no structured data yields a job whose hints are all unknown, so
        the classifier falls back to reading the text rather than assuming the role is open."""
        hints = make_job().hints
        assert hints.location_restrictions is None
        assert hints.timezone_restrictions is None

    def test_a_job_carries_a_boards_structured_restrictions(
        self, make_job: Callable[..., Job]
    ) -> None:
        job = make_job(
            hints=EligibilityHints(
                location_restrictions=("united states",), timezone_restrictions=()
            )
        )
        assert job.hints.location_restrictions == ("united states",)
        assert job.hints.timezone_restrictions == ()

    def test_the_old_flat_restriction_fields_are_gone(self, make_job: Callable[..., Job]) -> None:
        """They lived on Job because one board (Himalayas) had them, and defaulted to `[]`,
        which lied for every other board. The data now lives in `hints`, with `None` for
        unknown."""
        job = make_job()
        assert not hasattr(job, "location_restrictions")
        assert not hasattr(job, "timezone_restrictions")


class TestJobSearchText:
    def test_is_lower_cased(self, make_job: Callable[..., Job]) -> None:
        assert make_job(title="AI Engineer").search_text.startswith("ai engineer")

    def test_includes_title_description_and_location(self, make_job: Callable[..., Job]) -> None:
        text = make_job(
            title="AI Engineer", description="Build RAG systems", location="Worldwide"
        ).search_text
        assert "ai engineer" in text
        assert "build rag systems" in text
        assert "worldwide" in text


class TestFitScore:
    def test_defaults_to_no_match(self) -> None:
        score = FitScore()
        assert score.value == 0.0
        assert score.raw == 0
        assert score.matched == {}

    def test_value_must_be_a_normalized_fraction(self) -> None:
        """value is a 0.0-1.0 fraction, never a raw sum. Rejecting out-of-range guards against a
        caller passing the old integer sum and silently getting a nonsense score."""
        with pytest.raises(ValidationError):
            FitScore(value=10)
        with pytest.raises(ValidationError):
            FitScore(value=-0.1)


class TestScoredJob:
    def test_is_suitable_when_eligible(self, make_job: Callable[..., Job]) -> None:
        scored = ScoredJob(
            job=make_job(),
            fit=FitScore(value=1.0, raw=10, matched={"python": 10}),
            relevance=Relevance(keep=True, reason="title matches 'python'"),
            eligibility=Eligibility(status=EligibilityStatus.GLOBAL, reason="open worldwide"),
        )
        assert scored.is_suitable

    def test_is_not_suitable_when_excluded_however_good_the_fit(
        self, make_job: Callable[..., Job]
    ) -> None:
        scored = ScoredJob(
            job=make_job(),
            fit=FitScore(value=1.0, raw=99, matched={"python": 90, "rag": 9}),
            relevance=Relevance(keep=True, reason="title matches 'python'"),
            eligibility=Eligibility(
                status=EligibilityStatus.EXCLUDED_AUTHORIZATION, reason="US-only"
            ),
        )
        assert not scored.is_suitable

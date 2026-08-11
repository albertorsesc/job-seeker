"""Covers `job_seeker.domain.models`."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from pydantic import BaseModel, ValidationError

from job_seeker.domain.models import (
    ELIGIBLE_STATUSES,
    CurrencySource,
    Eligibility,
    EligibilityHints,
    EligibilityStatus,
    FitScore,
    Job,
    Relevance,
    SalaryPeriod,
    SalaryRange,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
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


class TestSalaryRange:
    """Two boards publish compensation as numbers; flattening it to a string at the adapter
    boundary threw that away, unrecoverably, before anything could reason about it.

    Same three-state discipline as EligibilityHints: `Job.salary is None` means the board said
    nothing, and a SalaryRange means it said something, even if only a floor or only free text.
    """

    def test_a_board_that_published_nothing_leaves_salary_absent(
        self, make_job: Callable[..., Job]
    ) -> None:
        assert make_job().salary is None

    def test_it_carries_the_bounds_and_currency_separately(self) -> None:
        salary = SalaryRange(
            minimum=120_000,
            maximum=160_000,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
        )
        assert (salary.minimum, salary.maximum, salary.currency) == (120_000, 160_000, "USD")

    def test_a_floor_with_no_ceiling_is_expressible(self) -> None:
        """ "From 120k" is a real posting, and is not the same fact as "120k to 120k"."""
        salary = SalaryRange(
            minimum=120_000, currency="USD", currency_source=CurrencySource.PUBLISHED
        )
        assert salary.minimum == 120_000
        assert salary.maximum is None

    def test_free_text_survives_when_a_board_publishes_no_numbers(self) -> None:
        """Some boards publish only prose. Keeping it means a reader still sees what was offered
        instead of a blank, without the pipeline pretending it parsed a number."""
        salary = SalaryRange(note="Competitive, DOE")
        assert salary.note == "Competitive, DOE"
        assert salary.minimum is None

    def test_it_is_frozen_because_it_is_something_a_board_reported(self) -> None:
        salary = SalaryRange(minimum=100, currency="USD", currency_source=CurrencySource.PUBLISHED)
        with pytest.raises(ValidationError):
            salary.minimum = 200

    def test_a_negative_bound_is_rejected_at_the_boundary(self) -> None:
        """A board sending a negative salary is bad data, and it should fail where it arrives
        rather than surface as a nonsense figure in a report."""
        with pytest.raises(ValidationError):
            SalaryRange(minimum=-1)

    def test_a_maximum_below_the_minimum_is_rejected(self) -> None:
        """An inverted range is not a range. Catching it here keeps every consumer from having to
        decide which of the two numbers to believe."""
        with pytest.raises(ValidationError):
            SalaryRange(minimum=200_000, maximum=100_000)

    def test_an_equal_minimum_and_maximum_is_a_fixed_rate_not_an_error(self) -> None:
        assert SalaryRange(minimum=150_000, maximum=150_000).maximum == 150_000

    def test_a_range_that_carries_no_claim_at_all_is_rejected(self) -> None:
        """Otherwise there are two ways to spell "no pay information", `Job.salary is None` and an
        empty SalaryRange, and the docstring's claim that absence lives on `Job.salary` is a
        convention rather than a guarantee. Dedup already reads presence of this object as a
        richness signal, so an empty one would count as rich.
        """
        with pytest.raises(ValidationError):
            SalaryRange()
        with pytest.raises(ValidationError):
            SalaryRange(
                currency="USD", currency_source=CurrencySource.PUBLISHED
            )  # a currency alone says nothing about pay

    def test_a_currency_without_a_stated_origin_is_rejected(self) -> None:
        """Both halves or neither. A currency whose origin is unrecorded cannot be told apart from
        one the engine invented, which is the entire reason the source field exists."""
        with pytest.raises(ValidationError):
            SalaryRange(minimum=100, currency="USD")
        with pytest.raises(ValidationError):
            SalaryRange(minimum=100, currency_source=CurrencySource.ASSUMED)

    def test_board_prose_alone_is_a_claim(self) -> None:
        assert SalaryRange(note="Competitive, DOE").note == "Competitive, DOE"


class TestAnnualizing:
    """Figures only compare once they are on one basis.

    Live Himalayas data returns 85 and 146,000 in the same currency on the same page, so ranking
    on the bare number puts an $85/hour role below a $60,000 one. The board's own figures stay
    exactly as published; the comparable ones are derived alongside them and labelled derived.
    """

    @pytest.mark.parametrize(
        "period,expected",
        [
            (SalaryPeriod.YEAR, 120_000),
            (SalaryPeriod.MONTH, 1_440_000),
            (SalaryPeriod.WEEK, 6_240_000),
            (SalaryPeriod.DAY, 31_200_000),
            (SalaryPeriod.HOUR, 249_600_000),
        ],
    )
    def test_each_period_converts_on_a_full_time_basis(
        self, period: SalaryPeriod, expected: float
    ) -> None:
        assert SalaryRange(minimum=120_000, period=period).annual_minimum == expected

    def test_the_hourly_case_this_exists_for(self) -> None:
        """The live posting that exposed the problem: $85/hour, which is not $85."""
        hourly = SalaryRange(
            minimum=85,
            maximum=85,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.HOUR,
        )
        assert hourly.annual_minimum == 176_800  # 85 x 2080
        assert hourly.minimum == 85  # what the board actually published is untouched

    def test_an_unknown_period_annualizes_to_nothing_rather_than_guessing(self) -> None:
        """The whole point of keeping the period nullable. A magnitude with no basis cannot be
        compared, and inventing one is how an hourly rate gets ranked as a salary."""
        unknown = SalaryRange(
            minimum=3255, maximum=4160, currency="USD", currency_source=CurrencySource.PUBLISHED
        )
        assert unknown.period is None
        assert unknown.annual_minimum is None
        assert unknown.annual_maximum is None

    def test_an_absent_bound_annualizes_to_nothing(self) -> None:
        floor = SalaryRange(minimum=120_000, period=SalaryPeriod.YEAR)
        assert floor.annual_minimum == 120_000
        assert floor.annual_maximum is None

    def test_the_derived_figures_cross_the_wire(self) -> None:
        """An MCP agent cannot apply the full-time assumption itself, so the conversion has to
        travel with the payload rather than staying a Python-side property."""
        payload = json.loads(
            SalaryRange(
                minimum=85,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.HOUR,
            ).model_dump_json()
        )
        assert payload["annual_minimum"] == 176_800
        assert payload["period"] == "hour"

    def test_two_postings_on_different_periods_become_comparable(self) -> None:
        """The end the whole feature serves."""
        hourly = SalaryRange(minimum=85, period=SalaryPeriod.HOUR)
        annual = SalaryRange(minimum=60_000, period=SalaryPeriod.YEAR)
        assert (hourly.minimum, annual.minimum) == (85, 60_000)  # the raw figures mislead
        ranked = sorted([hourly, annual], key=lambda s: s.annual_minimum or 0, reverse=True)
        assert ranked[0] is hourly  # annualized, the $85/hour role is the better paid one


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


class TestDerivedValuesAreInThePublishedContract:
    """Every derived value is a real field, so it appears in the validation schema the MCP tool
    publishes and an agent can read it off the contract it was handed rather than off prose.

    `computed_field` puts a value in the *serialization* schema only, which is the wrong one here.
    A real field plus a validator that recomputes on every construction keeps everything that gave:
    the value cannot drift, cannot be omitted, and cannot be faked by a caller. It adds the part
    that matters: the field is in the contract, marked read-only.
    """

    DERIVED: tuple[tuple[type[BaseModel], str], ...] = (
        (SearchResult, "all_sources_ran"),
        (SearchResult, "fully_scanned"),
        (Eligibility, "is_eligible"),
        (ScoredJob, "is_suitable"),
        (SourceCoverage, "failed"),
        (SalaryRange, "annual_minimum"),
        (SalaryRange, "annual_maximum"),
    )

    @pytest.mark.parametrize(
        "model,field", DERIVED, ids=lambda v: v if isinstance(v, str) else v.__name__
    )
    def test_it_appears_in_the_validation_schema_the_sdk_publishes(
        self, model: type[BaseModel], field: str
    ) -> None:
        assert field in model.model_json_schema()["properties"]

    @pytest.mark.parametrize(
        "model,field", DERIVED, ids=lambda v: v if isinstance(v, str) else v.__name__
    )
    def test_it_is_marked_read_only_so_a_caller_knows_not_to_send_it(
        self, model: type[BaseModel], field: str
    ) -> None:
        assert model.model_json_schema()["properties"][field].get("readOnly") is True

    def test_a_caller_cannot_fake_a_healthy_run(self) -> None:
        """The property `computed_field` had and a plain field would lose. The validator recomputes,
        so a payload claiming every board ran while carrying a failed one is corrected, not
        believed."""
        result = SearchResult(
            query=SearchQuery(),
            coverage=[SourceCoverage(source="a", error="boom")],
            all_sources_ran=True,
            fully_scanned=True,
        )
        assert result.all_sources_ran is False

    def test_a_caller_cannot_fake_eligibility(self) -> None:
        verdict = Eligibility(
            status=EligibilityStatus.EXCLUDED_LOCATION, reason="restricted", is_eligible=True
        )
        assert verdict.is_eligible is False

    def test_a_caller_cannot_fake_an_annual_figure(self) -> None:
        salary = SalaryRange(minimum=85, period=SalaryPeriod.HOUR, annual_minimum=1)
        assert salary.annual_minimum == 176_800

    def test_the_values_survive_a_json_round_trip(self) -> None:
        """They cross an MCP boundary, so the wire is where they have to hold."""
        original = SearchResult(
            query=SearchQuery(),
            coverage=[SourceCoverage(source="a", scanned=5, truncated=True)],
        )
        restored = SearchResult.model_validate_json(original.model_dump_json())
        assert (restored.all_sources_ran, restored.fully_scanned) == (True, False)

    def test_omitting_them_entirely_still_yields_the_right_answer(self) -> None:
        """No caller in this codebase passes them, and none should have to."""
        salary = SalaryRange(minimum=120_000, period=SalaryPeriod.YEAR)
        assert salary.annual_minimum == 120_000
        assert SourceCoverage(source="a").failed is False
        assert SourceCoverage(source="a", error="down").failed is True

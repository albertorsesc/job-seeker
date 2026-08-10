"""Core domain entities and value objects.

This module has no dependencies on any other layer. Everything else in the
package depends on these types, never the reverse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


class EligibilityStatus(StrEnum):
    """How a posting relates to the seeker's location/work-authorization needs.

    StrEnum rather than `(str, Enum)`: the latter keeps `Enum.__str__`, so a reporter
    interpolating a status would emit "EligibilityStatus.HOME_BASED" instead of "home-based",
    while JSON output stayed correct and hid the mistake.
    """

    HOME_BASED = "home-based"  # posting is located in the seeker's own country
    REGIONAL = "regional"  # posting explicitly allows the seeker's region (e.g. LATAM)
    GLOBAL = "global"  # hire-from-anywhere / worldwide
    REMOTE_UNVERIFIED = "remote-verify"  # remote, but eligibility not stated; confirm
    EXCLUDED_LOCATION = "excluded-location"  # location-restricted away from the seeker
    EXCLUDED_TIMEZONE = "excluded-timezone"  # timezone lock the seeker cannot meet
    EXCLUDED_AUTHORIZATION = "excluded-authorization"  # US-only / W2 / clearance / visa


ELIGIBLE_STATUSES: frozenset[EligibilityStatus] = frozenset(
    {
        EligibilityStatus.HOME_BASED,
        EligibilityStatus.REGIONAL,
        EligibilityStatus.GLOBAL,
        EligibilityStatus.REMOTE_UNVERIFIED,
    }
)


class SearchQuery(BaseModel):
    """A request for postings, interpreted by each source in its own dialect."""

    # No default: any default is one person's job title hardcoded into a reusable engine.
    # Callers pass the profile's search_terms, or their own.
    terms: list[str] = Field(default_factory=list)
    location: str | None = None
    remote: bool = True
    # How deep to read each board, NOT how many results to return. It is applied by the adapter
    # while fetching, so it bounds the candidate pool *before* anything is ranked: lowering it does
    # not trim the answer, it shrinks what the answer is chosen from, and the best-fitting posting
    # may simply never be fetched. It was called `max_results_per_source` and exposed as `--limit`,
    # which is what a caller reaches for when they want a shorter list, and they got a worse one.
    #
    # Bounded because an MCP tool exposes it to an agent that picks the number. Unbounded, a large
    # value walks every page of a six-figure feed, and a negative one means whatever each adapter's
    # slicing happens to do.
    scan_depth_per_source: int = Field(default=50, ge=1, le=1000)
    # How many ranked results to return. None means all of them. This is the one a caller wanting a
    # shorter list actually wants, and it applies after ranking, so it keeps the best rather than
    # the first fetched.
    max_results: int | None = Field(default=None, ge=1)
    max_age_days: int | None = Field(default=30, ge=1)


# Design notes for EligibilityHints, kept out of the docstring because that text is published in
# the MCP schema and read by an agent every session.
#
# - Most boards publish no structured eligibility data, so None is the default. An earlier shape
#   defaulted to [], which made "the board has no such field" indistinguishable from "the board
#   declared the role open to everyone", and silently promoted every posting from those boards to
#   unrestricted. Empty is a claim; absent is not.
# - Frozen, and tuples rather than lists, so these are genuinely immutable. `frozen=True` alone
#   blocks rebinding the attribute but not `hints.location_restrictions.append(...)`, and leaves
#   the model unhashable. Tuples close both. An adapter converts at construction, which is the
#   right place to mark mutable wire data becoming a settled fact. The JSON wire is an array.
class EligibilityHints(BaseModel):
    """What the board itself stated about who may hold the role.

    Three distinct states per field, and they must not be conflated: `null` means the board said
    nothing (the engine then reads the posting text instead), `[]` means it stated there is no
    restriction, and a populated list means the role is restricted to those places or UTC offsets.
    """

    model_config = ConfigDict(frozen=True)

    location_restrictions: tuple[str, ...] | None = None
    timezone_restrictions: tuple[float, ...] | None = None


class CurrencySource(StrEnum):
    """Whether a board stated the currency or its adapter supplied it.

    Both arrive on the wire as three identical letters, so without this a consumer cannot tell a
    fact from an inference. RemoteOK publishes no currency field at all and its adapter asserts USD
    from board convention; Himalayas publishes one per posting. An agent comparing 160,000 EUR
    against 170,000 USD is already on thin ice, and it should at least know which of the two units
    the engine was told and which it decided.
    """

    PUBLISHED = "published"  # the board stated it
    ASSUMED = "assumed"  # the adapter supplied it from what it knows of the board


class SalaryPeriod(StrEnum):
    """The unit a board quotes its pay figures in.

    Boards mix these freely and most publish no field saying which. Himalayas returns 85 and
    146,000 side by side in the same currency, so a figure without its period is not comparable to
    anything, and a pipeline that sorts on the bare number ranks an $85/hour role below a $60,000
    one. Each adapter declares how its own board expresses the period; the conversion lives here.
    """

    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"


# Periods per year, for normalizing figures onto one comparable basis.
#
# **These assume full-time.** 2,080 hours is 40 hours x 52 weeks, 260 days is 5 x 52. A part-time
# posting quoted hourly annualizes as though it were full-time, which overstates it. That is why
# the annualized figures are exposed as separate fields rather than replacing what the board
# published: the board's own numbers stay untouched and the derived ones are labelled derived.
_PERIODS_PER_YEAR: dict[SalaryPeriod, float] = {
    SalaryPeriod.HOUR: 2080.0,
    SalaryPeriod.DAY: 260.0,
    SalaryPeriod.WEEK: 52.0,
    SalaryPeriod.MONTH: 12.0,
    SalaryPeriod.YEAR: 1.0,
}


# Design notes for SalaryRange, deliberately comments rather than docstring. The class docstring
# is published verbatim in the MCP tool's JSON schema and read by an agent on every session, so it
# states what the type is; why it is shaped this way belongs to whoever maintains it.
#
# - Two boards report pay structured (Himalayas minSalary/maxSalary/currency, RemoteOK
#   salary_min/salary_max). This used to be flattened to a display string in the adapter, which
#   threw the numbers away irreversibly: a later reader had to re-parse a string we formatted
#   ourselves. Structure in, presentation at the edge, the direction the rest of the pipeline runs.
# - Every field is optional because boards disagree about what they know. A floor with no ceiling
#   is a different fact from a fixed rate.
# - Frozen, like EligibilityHints: a board reported this, the pipeline does not revise it.
# - Absence lives on `Job.salary`, which is None when the board said nothing at all, so "said
#   something unusable" and "said nothing" stay distinct.
# - `float`, not `Decimal`, and that is a decision. Nothing here accumulates error: figures are
#   carried, compared, and multiplied once by an integer factor. The cost is accepted and visible,
#   a CSV cell reads `150000.0`. Pydantic serializes Decimal to a JSON *string*, which would put
#   `"150000.0"` on the MCP wire and destroy the numeric comparison this shape exists to enable.
#   Revisit only if something ever aggregates pay across postings.
class SalaryRange(BaseModel):
    """Pay for one posting, as the board published it, plus a comparable annual equivalent.

    `minimum`/`maximum` are the board's own figures in `currency`, quoted per `period`. Any of them
    may be null when the board did not say. Compare postings on `annual_minimum`/`annual_maximum`,
    never on the raw figures: boards mix hourly and annual pay freely, so 85 and 146,000 can be the
    same currency on the same page. The annual figures are null when the period is unknown, and
    assume full time when it is.

    `annual_minimum` and `annual_maximum` arrive in the payload but are absent from this schema,
    because the MCP SDK publishes a validation-mode schema and pydantic omits computed fields from
    it. They are there; read them.
    """

    model_config = ConfigDict(frozen=True)

    minimum: float | None = Field(default=None, ge=0)
    maximum: float | None = Field(default=None, ge=0)
    # None when no currency is known. `""` would be a second way to say the same thing, and this
    # model already carries a docstring about never letting empty stand in for absent.
    currency: str | None = None
    # Where the currency came from. Absent exactly when the currency is.
    currency_source: CurrencySource | None = None
    # What the figures are quoted per. None means the board did not say and its adapter could not
    # establish it, which is a real and common answer: it is why the annualized fields below are
    # nullable rather than guessed.
    period: SalaryPeriod | None = None
    # Why the figures are what they are, in words, for the cases numbers cannot express. Two
    # things reach here: a board that publishes prose instead of a number ("Competitive, DOE"),
    # and a board that published figures this pipeline refused to trust, which says so explicitly
    # rather than restating them as a range someone would parse straight back out.
    #
    # Named `note`, not `raw`: `FitScore.raw` in this same module means the unnormalized number,
    # and one name meaning two things across sibling models is how a reader comes to expect
    # figures here.
    note: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def annual_minimum(self) -> float | None:
        """The lower bound as a yearly figure, or None when the period is unknown.

        Serialized, because the consumer that most needs a comparable number is an agent on the
        far side of the MCP wire, and it cannot convert without knowing the full-time assumption
        baked into `_PERIODS_PER_YEAR`. None is the honest answer for an unknown period: a
        magnitude alone cannot be compared, and inventing a basis is how an hourly rate comes to
        be ranked as a salary.
        """
        return self._annualized(self.minimum)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def annual_maximum(self) -> float | None:
        """The upper bound as a yearly figure, or None when the period is unknown."""
        return self._annualized(self.maximum)

    def _annualized(self, value: float | None) -> float | None:
        if value is None or self.period is None:
            return None
        return round(value * _PERIODS_PER_YEAR[self.period], 2)

    @model_validator(mode="after")
    def _says_something(self) -> Self:
        """Reject a range that carries no claim at all, and one that runs backwards.

        Absence belongs on `Job.salary`, which is `None` when the board said nothing. Without this,
        `SalaryRange()` also constructs, so there are two ways to spell "no pay information" and
        the docstring above is a convention rather than a guarantee. That is exactly the state
        `EligibilityHints` exists as a warning about, and the dedup richness score already reads
        presence of this object as a signal, so an empty one would count as rich.

        An upper bound below the lower one is not a range either: letting it through would make
        every consumer decide which of the two numbers to believe. Equal bounds are a fixed rate,
        not an error.
        """
        if (self.currency is None) != (self.currency_source is None):
            raise ValueError(
                "currency and currency_source are set together or not at all; a currency with no "
                "stated origin cannot be told apart from one the engine invented."
            )
        if self.minimum is None and self.maximum is None and not self.note:
            raise ValueError(
                "a SalaryRange must carry a figure or the board's own text; "
                "use None on Job.salary to mean the board said nothing."
            )
        if self.minimum is not None and self.maximum is not None and self.maximum < self.minimum:
            raise ValueError(
                f"salary maximum ({self.maximum}) is below the minimum ({self.minimum})."
            )
        return self


class Job(BaseModel):
    """A canonical, source-agnostic job posting.

    Every source adapter normalizes its native payload into this shape, so the
    scoring, filtering and reporting layers never learn a source's quirks.
    """

    title: str
    company: str
    url: str
    source: str
    description: str = ""
    location: str = ""
    # None means the board published nothing about pay. See SalaryRange for why that is not the
    # same as a range with no numbers in it.
    salary: SalaryRange | None = None
    posted_at: datetime | None = None
    seniority: str = ""
    employment_type: str = ""
    # What the board said about who may hold the role. See EligibilityHints for why absent and
    # empty are different facts and must not collapse.
    hints: EligibilityHints = Field(default_factory=EligibilityHints)

    @field_validator("posted_at")
    @classmethod
    def _ensure_aware(cls, value: datetime | None) -> datetime | None:
        """Stamp a naive datetime as UTC so posted_at is always tz-aware.

        Adapters normalize dates from many formats and one may hand back a naive datetime.
        Comparing a naive and an aware datetime raises at runtime, and that comparison happens
        deep in dedup ranking and age filtering, single-threaded, where it would abort the whole
        run. Fixing it at the boundary means nothing downstream has to guard.
        """
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @property
    def search_text(self) -> str:
        """Lower-cased haystack used by scorers and filters."""
        return f"{self.title}\n{self.description}\n{self.location}".lower()


# Design notes for FitScore, kept out of the published schema.
#
# - `value` is normalized because a raw sum could not be compared: 10 was a strong fit for a
#   four-skill profile and weak for a thirty-skill one, and a caller thresholding it had no way to
#   learn the scale, least of all an agent that only ever sees the number.
# - `raw` is the exact integer the pipeline ranks on, so ordering never turns on float rounding.
class FitScore(BaseModel):
    """How well a posting matches the seeker's weighted profile signals.

    `value` is 0.0-1.0, the share of the profile's total available weight this posting matched, so
    it means the same thing across different profiles. `matched` names which of the seeker's skill
    patterns contributed which weight, so the score can be explained rather than just stated.
    """

    value: float = Field(default=0.0, ge=0.0, le=1.0)
    raw: int = Field(default=0, ge=0)
    matched: dict[str, int] = Field(default_factory=dict)


class Relevance(BaseModel):
    """Why a posting is, or is not, what the seeker searched for.

    Relevance is the loosest, most heuristic stage, and it used to drop a posting with no record
    of why, while every other stage explained itself (`FitScore.matched`, `Eligibility.reason`).
    Recording the verdict and its reason makes "why is this job here?" answerable and keeps the
    noisiest filter as accountable as the rest. A surviving `ScoredJob` always carries `keep=True`;
    the reason is what earned it.
    """

    keep: bool
    reason: str


class Eligibility(BaseModel):
    """Whether, and how, the seeker can hold this role.

    Carries a derived `is_eligible` boolean in the payload that is absent from this schema (the SDK
    publishes a validation-mode schema, which omits computed fields). Use it rather than
    reimplementing which of the seven statuses are holdable.
    """

    status: EligibilityStatus
    # Required, not defaulted: the reason is the product. The classifier sets one on every path,
    # so a reasonless Eligibility is a bug, and the type should make it impossible rather than
    # quietly accept an empty string.
    reason: str

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_eligible(self) -> bool:
        """Serialized, not merely computed.

        Which of seven statuses are holdable is domain knowledge, and the consumers that most
        need it are on the far side of a wire: an MCP agent receiving `{"status":
        "remote-verify"}` would otherwise have to reimplement ELIGIBLE_STATUSES to read it.

        It is in the payload, not in the tool's published schema. A computed field appears in the
        serialization schema only, and FastMCP builds its output schema in validation mode, where
        computed fields are absent. `find_jobs` is annotated `-> dict[str, Any]` besides, so no
        output schema is published at all. The agent receives this field and is not told to expect
        it, which is worth fixing on the tool, not here.
        """
        return self.status in ELIGIBLE_STATUSES


class ScoredJob(BaseModel):
    """A posting decorated with why it survived each stage. The pipeline's output unit.

    It carries the reasoning of all three judgment stages: `fit` (how well it matches, and which
    signals earned that), `relevance` (why it is on-topic), and `eligibility` (whether and why the
    seeker can hold it). A consumer never has to guess why a posting is in the result.
    """

    job: Job
    fit: FitScore
    relevance: Relevance
    eligibility: Eligibility

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_suitable(self) -> bool:
        return self.eligibility.is_eligible


class SourceOutcome(BaseModel):
    """What every source reports about a run of itself, whether mid-flight or in the report.

    `scanned` and `truncated` are not diagnostics, they are part of the answer. A run that
    examined 200 of a board's 103,000 postings and a run that examined all of them are
    different facts, and without them the caller can only honestly say "here are the best of
    whatever I happened to look at".

    `error` rather than an exception: a source failing is an expected outcome, not an
    exceptional one, because several boards are fetched concurrently and any of them can be
    down.
    """

    source: str
    scanned: int = 0
    truncated: bool = False
    error: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed(self) -> bool:
        return bool(self.error)


class SourceResult(SourceOutcome):
    """What one source returned. The outcome, plus the postings themselves.

    A failed result carries no jobs and says why.
    """

    jobs: list[Job] = Field(default_factory=list)


class SourceCoverage(SourceOutcome):
    """How one source performed in a finished run. The outcome, plus how many survived.

    The postings are deliberately absent: they are ranked together in `SearchResult.jobs`, and
    repeating them per source would bloat the payload and invite the two copies to disagree.
    """

    kept: int = 0


class SearchResult(BaseModel):
    """A finished run: the ranked postings, and the truth about how they were found.

    Two derived booleans arrive in the payload and are NOT in this schema, because the MCP SDK
    publishes a validation-mode schema and pydantic omits computed fields from it. Read them:
    `all_sources_ran` is false when a board failed, which means whole categories of job are missing
    from `jobs`; `fully_scanned` is false when a board was read only to `scan_depth_per_source`,
    which is the ordinary case. Say so when reporting results if the first is false.

    Coverage travels with the jobs rather than going to a log, because the consumer that most
    needs it is an agent on the far end of an MCP call which never sees stderr. A run where
    three of five boards failed must not be indistinguishable from a healthy one, or the
    agent will tell the seeker "here are the best jobs you can hold" on the strength of two
    boards and no caveat.
    """

    query: SearchQuery
    jobs: list[ScoredJob] = Field(default_factory=list)
    coverage: list[SourceCoverage] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_sources_ran(self) -> bool:
        """True when at least one source ran and none of them failed.

        The rare fact, and the alarming one. A board being unreachable means whole categories of
        job are missing from the answer, and nothing else in the payload says so.

        The `bool(self.coverage)` guard is the whole point: `any([])` is False, so without it a run
        where zero sources executed, the most incomplete run there is, would report itself as
        healthy. That failure is silent, because an empty result that claims to be complete looks
        exactly like "nothing matched".
        """
        return bool(self.coverage) and not any(c.failed for c in self.coverage)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fully_scanned(self) -> bool:
        """True when every source was read to the end rather than capped.

        The common fact, and a mild one. `max_results_per_source` defaults to 50 against a feed of
        ~98,000, so an ordinary healthy run is not fully scanned and never will be.

        Split from `all_sources_ran` because one boolean covering both was false on every single
        run, which is the same as being absent: a reader learns to skip it, and then skips it on
        the day a board is actually down. Two facts, two names, and the rare one stays rare.
        """
        return bool(self.coverage) and not any(c.truncated for c in self.coverage)

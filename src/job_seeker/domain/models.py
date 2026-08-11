"""Core domain entities and value objects.

This module has no dependencies on any other layer. Everything else in the
package depends on these types, never the reverse.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from job_seeker.domain.memory import PostingDecision
from job_seeker.domain.regions import canonical_place


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


# Statuses where a board affirmatively said the seeker may hold the role. `remote-verify` is
# deliberately absent: it means nobody said, which is a lead rather than a fact.
#
# Two tiers, not four. An earlier version ranked home-based above global above regional, on the
# reasoning that a directly named country is surer than one reached through the region map. Live
# data killed it: that ordering put a 3% Node.js role above a 26% Senior AI Engineer, because the
# difference between those statuses is geography, not confidence, and all three are equally
# stated. What a seeker actually wants separated is "a board cleared me" from "nobody said".
STATED_STATUSES: frozenset[EligibilityStatus] = frozenset(
    {EligibilityStatus.HOME_BASED, EligibilityStatus.GLOBAL, EligibilityStatus.REGIONAL}
)


class SortOrder(StrEnum):
    """How to rank what survived the filters.

    Neither order is right for everyone, which is why this is a choice rather than a default
    change. `FIT` answers "what suits my skills"; a 26% posting the board never cleared can outrank
    a 3% one it did. `CONFIDENCE` puts every posting a board affirmatively cleared above every
    posting nobody did, and sorts by fit inside each group.
    """

    FIT = "fit"
    CONFIDENCE = "confidence"


# How many postings a board is read for, by default and at most.
#
# One number, declared here, because the CLI and the MCP tool both have to state it and two
# hardcoded copies that must agree will drift. Each entrypoint reads it rather than restating it.
DEFAULT_SCAN_DEPTH = 1000


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
    #
    # The default is the ceiling, because reading shallowly was measurably costing the answer. At
    # 50 the largest board contributed nothing at all: its feed is recency-ordered across every
    # category, so the first 50 postings are simply the newest 50, and none of them survived to the
    # ranking. Measured on one profile, raising it to 1000 took a search from about 1 second to
    # about 13, moved the best eligible match from 34% fit to 52%, and put three roles from that
    # board into the top five. Time is the cheap resource here and the pool is the scarce one.
    scan_depth_per_source: int = Field(default=DEFAULT_SCAN_DEPTH, ge=1, le=DEFAULT_SCAN_DEPTH)
    # How many ranked results to return. None means all of them. This is the one a caller wanting a
    # shorter list actually wants, and it applies after ranking, so it keeps the best rather than
    # the first fetched.
    max_results: int | None = Field(default=None, ge=1)
    # Bounded for the same reason as the scan depth, and with a sharper edge: `age_cutoff`
    # subtracts this many days from now, and a number too large for a C int raises OverflowError
    # out of `fetch` rather than returning a date. Ten years is already far past any real use, and
    # None is how a caller says "no age window" without reaching for a huge number to mean it.
    max_age_days: int | None = Field(default=30, ge=1, le=3650)
    # Drop postings whose eligibility nobody stated, for this search only. The profile's
    # `include_unverified` is the standing preference; this narrows further and never widens, so a
    # seeker who has already opted out of unverified postings cannot accidentally opt back in.
    stated_only: bool = False
    # Drop postings below this fit, for this search only. 0.0 keeps everything, which is the
    # default and the only value that means "no fit filtering".
    #
    # It earns its place because ranking alone stopped being enough once the engine read deeply.
    # Measured on one profile against a full-depth run: 50 postings survived eligibility, the best
    # fitting 52%, and the median 4%. The list is mostly postings the seeker will never want, and
    # it is the ranking that hides that rather than the filter.
    #
    # Compared against `FitScore.value`, which is the share of the profile's whole weight a posting
    # matched, so a useful threshold is far below what the word "percent" suggests: no real posting
    # names every skill a seeker has.
    min_fit: float = Field(default=0.0, ge=0.0, le=1.0)
    # Only postings the engine has not shown this seeker before. Ignored, not honoured, when
    # memory could not be read: an empty list reads as "nothing new this week", which is the one
    # lie that costs a seeker a job without their ever knowing it was told.
    new_only: bool = False
    # Postings the seeker dismissed are hidden by default. This asks for them back, for the run
    # where they want to check what they threw away.
    include_dismissed: bool = False
    sort: SortOrder = SortOrder.FIT


# Marks a field the engine derives and the consumer only ever receives. `readOnly` is the JSON
# Schema way of saying so, and it is what makes a derived field honest as a real field: the
# contract carries the value AND states that supplying it is meaningless.
_DERIVED: dict[str, Any] = {"readOnly": True}


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

    # The comparable form of `location_restrictions`, derived on every construction.
    #
    # The same reason `SalaryRange` carries an annual figure beside the board's own: a value
    # nobody can compare is not much of a fact. Four boards name one country four ways, and
    # "United States of America", "USA" and "United States" are the same restriction written by
    # Himalayas, a profile and WeWorkRemotely. Carried rather than recomputed at each comparison,
    # because it was recomputed in two places in two orders and they disagreed.
    #
    # The board's own words stay above, and they are what a reason string and a report show. This
    # is for deciding, not for reading.
    canonical_locations: tuple[str, ...] | None = Field(
        default=None,
        json_schema_extra=_DERIVED,
        description=(
            "`location_restrictions` reduced to one name per place, so restrictions from "
            "different boards can be compared. Derived: sent to you, never sent by you."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_canonical(cls, data: Any) -> Any:
        """Canonicalize before validation, because this model is frozen.

        `None` survives as `None` and `()` as `()`: absent and "no restriction" are different
        claims, and this whole model exists to keep them apart.
        """
        if not isinstance(data, dict):
            return data
        stated = data.get("location_restrictions")
        canonical = (
            None
            if stated is None
            else tuple(dict.fromkeys(canonical_place(str(place)) for place in stated))
        )
        return {**data, "canonical_locations": canonical}


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


def _annualize(value: Any, factor: float | None) -> float | None:
    """One bound as a yearly figure, or None when either the bound or the period is unknown."""
    if factor is None or not isinstance(value, int | float) or isinstance(value, bool):
        return None
    return round(float(value) * factor, 2)


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

    # The comparable figures, derived from the bounds and the period on every construction.
    #
    # Real fields rather than `computed_field`, because they are part of the contract an MCP agent
    # is handed and pydantic puts a computed field in the *serialization* schema only, while the
    # SDK publishes the *validation* schema. A derived value absent from the contract describing it
    # is a value the consumer has to be told about in prose, which is not a contract.
    #
    # `_derive_annual` recomputes them from the bounds on every construction, including from JSON,
    # so they cannot be omitted, cannot drift, and cannot be faked by a caller.
    annual_minimum: float | None = Field(
        default=None,
        json_schema_extra=_DERIVED,
        description=(
            "The lower bound as a yearly figure, assuming full time. Null when the period is "
            "unknown, because a magnitude with no basis cannot be compared. Derived: sent to you, "
            "never sent by you."
        ),
    )
    annual_maximum: float | None = Field(
        default=None,
        json_schema_extra=_DERIVED,
        description="The upper bound as a yearly figure. Derived; see annual_minimum.",
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_annual(cls, data: Any) -> Any:
        """Annualize the bounds before validation, because this model is frozen.

        `mode="after"` cannot assign on a frozen model, and unfreezing it to make the derivation
        convenient would trade a real guarantee for a convenience: these are figures a board
        reported, and nothing downstream may revise them.
        """
        if not isinstance(data, dict):
            return data
        period = data.get("period")
        period = SalaryPeriod(period) if isinstance(period, str) else period
        factor = _PERIODS_PER_YEAR.get(period) if period is not None else None
        return {
            **data,
            "annual_minimum": _annualize(data.get("minimum"), factor),
            "annual_maximum": _annualize(data.get("maximum"), factor),
        }

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
    """Whether, and how, the seeker can hold this role."""

    status: EligibilityStatus
    # Required, not defaulted: the reason is the product. The classifier sets one on every path,
    # so a reasonless Eligibility is a bug, and the type should make it impossible rather than
    # quietly accept an empty string.
    reason: str

    is_eligible: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description=(
            "Whether this status is one the seeker can hold. Derived from `status`, so you never "
            "have to reimplement which of the seven count. Sent to you, never sent by you."
        ),
    )

    @model_validator(mode="after")
    def _derive_eligible(self) -> Self:
        self.is_eligible = self.status in ELIGIBLE_STATUSES
        return self


class PostingHistory(BaseModel):
    """What memory knew about this posting *before* this run.

    Carried on a `ScoredJob` beside `fit`, `relevance` and `eligibility`, so a posting explains its
    own past the way it already explains its fit and whether the seeker may hold it.
    """

    key: str
    handle: str
    # Deliveries before this run, so a first sighting reads 0 rather than 1. The seeker sees "shown
    # 3 times since 12 Jul", which is what lets them sanity-check a NEW badge rather than trust it,
    # and what makes a badge that is wrong visible rather than merely wrong.
    times_seen: int = 0
    first_seen_at: datetime | None = None
    decision: PostingDecision | None = None
    decided_at: datetime | None = None

    is_new: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description=(
            "Whether this run is the first time the engine has shown you this posting. Not whether "
            "the board posted it recently. Derived: sent to you, never sent by you."
        ),
    )

    @model_validator(mode="after")
    def _derive_is_new(self) -> Self:
        self.is_new = self.first_seen_at is None
        return self


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
    # What memory knew about this posting before this run. None when memory could not answer, which
    # is not the same as never having seen it: see `Recollection.available`.
    history: PostingHistory | None = None

    is_suitable: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description="Whether the seeker can hold this posting. Derived from `eligibility`.",
    )

    @model_validator(mode="after")
    def _derive_suitable(self) -> Self:
        self.is_suitable = self.eligibility.is_eligible
        return self


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

    failed: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description="Whether this source failed. Derived from `error` being non-empty.",
    )

    @model_validator(mode="after")
    def _derive_failed(self) -> Self:
        self.failed = bool(self.error)
        return self


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


class MemoryStatus(BaseModel):
    """Whether this run could remember, and what remembering changed about the answer.

    Travels with the result for the same reason coverage does: the consumer most in need of it is
    an agent on the far end of an MCP call that never sees stderr. A run whose memory was
    unreadable looks exactly like an ordinary run, except that nothing is marked new and every
    posting the seeker banned is back in the list. Saying so is the difference between a caveat and
    a silent regression.
    """

    enabled: bool = True  # false when the seeker turned memory off for this run
    available: bool = False  # the journal could be read
    recorded: bool = False  # this run's postings were written back
    error: str = ""  # why the read failed
    write_error: str = ""  # why the write failed. Separate: a read can work when a write cannot.
    known: int = 0  # postings remembered before this run
    # Counted before `max_results`, matching how `SourceCoverage.kept` is counted, so a `new` larger
    # than the new postings actually returned is exactly how a caller sees the cap bite.
    new: int = 0
    dismissed_hidden: int = 0
    not_new_hidden: int = 0
    previous_run_at: datetime | None = None

    healthy: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description=(
            "Whether memory worked end to end this run. When false, nothing is marked new and "
            "postings the seeker dismissed are NOT hidden. Derived; say so when reporting."
        ),
    )

    @model_validator(mode="after")
    def _derive_healthy(self) -> Self:
        self.healthy = (
            self.enabled
            and self.available
            and self.recorded
            and not (self.error or self.write_error)
        )
        return self


class SearchResult(BaseModel):
    """A finished run: the ranked postings, and the truth about how they were found.

    Coverage travels with the jobs rather than going to a log, because the consumer that most
    needs it is an agent on the far end of an MCP call which never sees stderr. A run where
    three of five boards failed must not be indistinguishable from a healthy one, or the
    agent will tell the seeker "here are the best jobs you can hold" on the strength of two
    boards and no caveat.
    """

    query: SearchQuery
    jobs: list[ScoredJob] = Field(default_factory=list)
    coverage: list[SourceCoverage] = Field(default_factory=list)
    memory: MemoryStatus = Field(default_factory=MemoryStatus)

    # The two honesty flags, derived from coverage on every construction.
    #
    # The `bool(self.coverage)` guard in each is the whole point: `any([])` is False, so without it
    # a run where zero sources executed, the most incomplete run there is, would report itself as
    # healthy. That failure is silent, because an empty result claiming to be complete looks
    # exactly like "nothing matched".
    #
    # They are two flags rather than one because a single boolean covering both was false on every
    # run, which is the same as being absent: a reader learns to skip it, and then skips it on the
    # day a board is actually down.
    all_sources_ran: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description=(
            "False when a board failed, which means whole categories of job are missing from "
            "`jobs`. Say so when reporting results. Derived: sent to you, never sent by you."
        ),
    )
    fully_scanned: bool = Field(
        default=False,
        json_schema_extra=_DERIVED,
        description=(
            "False when a board was read only as deep as `scan_depth_per_source`, which is the "
            "ordinary case against a six-figure feed. Derived."
        ),
    )

    @model_validator(mode="after")
    def _derive_coverage_flags(self) -> Self:
        self.all_sources_ran = bool(self.coverage) and not any(c.failed for c in self.coverage)
        self.fully_scanned = bool(self.coverage) and not any(c.truncated for c in self.coverage)
        return self

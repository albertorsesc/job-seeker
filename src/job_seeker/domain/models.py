"""Core domain entities and value objects.

This module has no dependencies on any other layer. Everything else in the
package depends on these types, never the reverse.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


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
    # Bounded because an MCP tool exposes these to an agent that picks the numbers. Unbounded,
    # a large value walks every page of a six-figure feed and a negative one means whatever
    # each adapter's slicing happens to do.
    max_results_per_source: int = Field(default=50, ge=1, le=1000)
    max_age_days: int | None = Field(default=30, ge=1)


class EligibilityHints(BaseModel):
    """What a board published about who may hold a role.

    Three states, and collapsing any two of them is a bug the whole eligibility layer is built
    to avoid:

    - `None`: the board said nothing. Fall back to reading the posting text.
    - `[]`: the board said explicitly there is no restriction. The role is open.
    - `["united states", ...]`: restricted to these.

    Most boards publish no structured eligibility data, so `None` is the default. Defaulting to
    `[]` instead, as an earlier shape did, made "the board has no such field" indistinguishable
    from "the board declared the role open to everyone", and silently promoted every posting
    from those boards to unrestricted. Empty is a claim; absent is not.

    Frozen, and tuples rather than lists, so these are genuinely immutable: they are facts a
    board reported, not values the pipeline revises. `frozen=True` alone would block rebinding
    the attribute but not `hints.location_restrictions.append(...)`, and it would leave the model
    unhashable. Tuples close both. An adapter turns a board's list into a tuple at construction,
    which is the right place to mark mutable wire data becoming a settled fact. The JSON wire is
    an array regardless.
    """

    model_config = ConfigDict(frozen=True)

    location_restrictions: tuple[str, ...] | None = None
    timezone_restrictions: tuple[float, ...] | None = None


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
    salary: str = ""
    posted_at: datetime | None = None
    seniority: str = ""
    employment_type: str = ""
    # What the board said about who may hold the role. See EligibilityHints for why absent and
    # empty are different facts and must not collapse.
    hints: EligibilityHints = Field(default_factory=EligibilityHints)

    @property
    def search_text(self) -> str:
        """Lower-cased haystack used by scorers and filters."""
        return f"{self.title}\n{self.description}\n{self.location}".lower()


class FitScore(BaseModel):
    """How well a posting matches the seeker's profile signals."""

    value: int = 0
    matched: list[str] = Field(default_factory=list)


class Eligibility(BaseModel):
    """Whether, and how, the seeker can hold this role."""

    status: EligibilityStatus
    reason: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_eligible(self) -> bool:
        """Serialized, not merely computed.

        Which of seven statuses are holdable is domain knowledge, and the consumers that most
        need it are on the far side of a wire: an MCP agent receiving `{"status":
        "remote-verify"}` would otherwise have to reimplement ELIGIBLE_STATUSES to read it.
        `computed_field` also puts it in the serialization JSON schema, so the MCP tool
        contract documents it to the agent for free.
        """
        return self.status in ELIGIBLE_STATUSES


class ScoredJob(BaseModel):
    """A posting decorated with fit and eligibility. The pipeline's output unit."""

    job: Job
    fit: FitScore
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
    def is_complete(self) -> bool:
        """True only when at least one source ran, and every source that ran ran fully.

        The `bool(self.coverage)` guard is the whole point: `any([])` is False, so without it
        a run where zero sources executed, the most incomplete run there is, would report
        itself as complete. That is the exact failure this class exists to prevent, and it is
        silent, because an empty result with `is_complete=True` looks like "no jobs matched".
        """
        return bool(self.coverage) and not any(c.failed or c.truncated for c in self.coverage)

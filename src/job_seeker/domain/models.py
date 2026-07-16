"""Core domain entities and value objects.

This module has no dependencies on any other layer. Everything else in the
package depends on these types, never the reverse.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
    # Structured eligibility hints when a source provides them (e.g. Himalayas).
    location_restrictions: list[str] = Field(default_factory=list)
    timezone_restrictions: list[float] = Field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """Stable identity for dedup: URL when present, else title+company."""
        basis = (
            self.url.strip().lower()
            or f"{self.title.strip().lower()}|{self.company.strip().lower()}"
        )
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()

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

    @property
    def is_eligible(self) -> bool:
        return self.status in ELIGIBLE_STATUSES


class ScoredJob(BaseModel):
    """A posting decorated with fit and eligibility. The pipeline's output unit."""

    job: Job
    fit: FitScore
    eligibility: Eligibility

    @property
    def is_suitable(self) -> bool:
        return self.eligibility.is_eligible

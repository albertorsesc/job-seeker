"""Core domain: entities, value objects, and the seeker profile.

This package depends on nothing else in job-seeker. Every other layer depends on it, never
the reverse. Import the public types from here rather than reaching into the submodules, so
the internal layout stays free to change:

    from job_seeker.domain import Job, Profile, ScoredJob
"""

from job_seeker.domain.models import (
    ELIGIBLE_STATUSES,
    Eligibility,
    EligibilityHints,
    EligibilityStatus,
    FitScore,
    Job,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
    SourceOutcome,
    SourceResult,
)
from job_seeker.domain.profile import (
    EligibilityRules,
    LocationProfile,
    Profile,
)

__all__ = [
    "ELIGIBLE_STATUSES",
    "Eligibility",
    "EligibilityHints",
    "EligibilityRules",
    "EligibilityStatus",
    "FitScore",
    "Job",
    "LocationProfile",
    "Profile",
    "ScoredJob",
    "SearchQuery",
    "SearchResult",
    "SourceCoverage",
    "SourceOutcome",
    "SourceResult",
]

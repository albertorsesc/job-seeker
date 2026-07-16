"""The seeker's profile: the single source of truth for what "suitable" means.

The profile is authored as a Markdown file with a YAML front-matter block (see
`config.profile_loader`). Scoring weights, eligibility rules and role filters all
come from here, so the engine is fully reusable: swap the profile, keep the code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LocationProfile(BaseModel):
    country: str = "Worldwide"
    # UTC offset of the seeker's working timezone, e.g. -6 for Mexico City.
    timezone_utc_offset: float = 0.0


class EligibilityRules(BaseModel):
    """Declarative rules the eligibility classifier applies."""

    # Region names (lower-cased) that count as "I may work here". Empty means the rule is off.
    # Do not add a work mode such as "remote" here: it is not a region, it appears in nearly
    # every posting this engine ingests, and it would pass everything through the filter.
    eligible_regions: list[str] = Field(default_factory=list)
    # Drop postings that demand US residency / work authorization / clearance.
    exclude_us_only: bool = True
    # Drop postings whose timezone window cannot include the seeker's offset.
    exclude_timezone_locked: bool = True
    # Offsets (hours) considered acceptable overlap with the seeker's timezone.
    acceptable_timezone_offsets: list[float] = Field(default_factory=list)


class Profile(BaseModel):
    """Machine-readable seeker profile parsed from the Markdown front matter."""

    name: str = "Anonymous"
    headline: str = ""
    seniority: str = ""
    location: LocationProfile = Field(default_factory=LocationProfile)
    eligibility: EligibilityRules = Field(default_factory=EligibilityRules)

    # Weighted signals: regex pattern -> weight. Higher weight = stronger fit.
    skills: dict[str, int] = Field(default_factory=dict)

    # A posting title must contain one of these to be a role the seeker wants. Empty means the
    # rule is off. No default: an engineering vocabulary here would silently filter out every
    # posting for a seeker who is not an engineer.
    role_include: list[str] = Field(default_factory=list)
    # Titles matching these are never relevant (marketing, sales, etc.).
    role_exclude: list[str] = Field(default_factory=list)
    # Titles where a matched keyword is a human role, not the thing the seeker builds
    # (e.g. an AI seeker filtering out "support agent").
    false_positive_terms: list[str] = Field(default_factory=list)

    # What to search for. Supplied by the profile; there is no sensible default, because any
    # default would be one person's job title baked into a reusable engine. A profile with no
    # search terms is a configuration error for the loader to reject, not something to guess at.
    search_terms: list[str] = Field(default_factory=list)

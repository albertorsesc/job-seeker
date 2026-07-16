"""The seeker's profile: the single source of truth for what "suitable" means.

The profile is authored as a Markdown file with a YAML front-matter block (see
`config.profile_loader`). Scoring weights, eligibility rules and role filters all
come from here, so the engine is fully reusable: swap the profile, keep the code.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LocationProfile(BaseModel):
    country: str = "Worldwide"
    # UTC offset of the seeker's working timezone, e.g. -6 for Mexico City.
    timezone_utc_offset: float = 0.0


class EligibilityRules(BaseModel):
    """What the seeker will accept, as data the profile supplies. Empty means the rule is off.

    Every rule is a value, never a switch. An earlier shape had `exclude_us_only: bool`, which
    could only mean "run a US-specific term list hidden in the classifier": a country baked into
    an engine that claims to serve a seeker anywhere, and the profile-driven principle failing in
    the one module that is the product. So the terms themselves live here.

    "Empty means off" is the consistent reading across every field: a seeker who states no
    constraint gets none invented for them. Empty is never "match everything" and never "match
    nothing".
    """

    # Region names (lower-cased) that count as "I may work here". Do not add a work mode such as
    # "remote": it is not a region, it appears in nearly every posting this engine ingests, and
    # it would pass everything through the filter.
    eligible_regions: list[str] = Field(default_factory=list)
    # Phrases that signal a work authorization the seeker does not hold, matched against the
    # posting text, e.g. ["us citizen", "green card", "security clearance", "w2 only"].
    disqualifying_authorization_terms: list[str] = Field(default_factory=list)
    # Phrases that signal a location lock, e.g. ["us only", "eu only", "us timezone"].
    location_lock_terms: list[str] = Field(default_factory=list)
    # How many hours a demanded timezone may sit from `location.timezone_utc_offset` before it
    # excludes the seeker. One number measured against the offset the profile already declares,
    # so it cannot drift from it the way a hand-listed set of acceptable offsets did. None means
    # timezone never excludes. Non-negative: a negative bound would exclude every posting, which
    # is the silent "match nothing" this shape exists to rule out. Zero is valid (exact offset).
    max_timezone_distance_hours: float | None = Field(default=None, ge=0)
    # Whether a posting whose eligibility could not be determined still counts as eligible.
    # Default True favours recall: a missed real job costs the seeker more than reading one
    # unconfirmable posting. A seeker who wants only confirmed-eligible roles sets this false.
    include_unverified: bool = True

    @field_validator("eligible_regions", "disqualifying_authorization_terms", "location_lock_terms")
    @classmethod
    def _normalize_terms(cls, value: list[str]) -> list[str]:
        """Lower-case and strip every term, and drop the blanks.

        These are matched against `Job.search_text`, which is lower-cased, so a term authored as
        "US Only" could never match: the exact silent-no-match this redesign is built to kill.
        Blanks are dropped because an empty string is a substring of every posting, so one stray
        blank line in the YAML would quietly match everything. Skills are deliberately NOT
        normalized here: they are regexes compiled case-insensitively by the scorer, a different
        contract.
        """
        return [term.strip().lower() for term in value if term.strip()]


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

"""Decide whether, and how, the seeker can hold a role.

The product's promise is "jobs you can actually hold", so this is its heart. Every rule reads from
the profile; no country, region, or timezone is hardcoded, so the same classifier serves a seeker
anywhere. A domain service, pure reasoning over a Job and a Profile.

Two paths. When a board reports structured restrictions (`EligibilityHints`), decide from them:
they are precise. When it reports nothing (hints are None), fall back to reading the posting text
against the profile's term lists. The None-vs-empty distinction on the hints is what selects the
path: `None` means "the board said nothing, read the text", `()` means "the board said no
restriction".

The one universal set is `_GLOBAL_SIGNALS`: words that mean "hireable anywhere". These help every
seeker regardless of location, so they are not candidate-specific and are the single built-in
vocabulary here. "remote" is deliberately not among them: it is a work mode, not a claim of global
eligibility, and it appears in nearly every posting.
"""

from __future__ import annotations

import re

from job_seeker.domain.models import Eligibility, EligibilityStatus, Job
from job_seeker.domain.profile import Profile

# Phrases in posting text that mean "hireable anywhere". Multi-word on purpose: a bare "global"
# or "globally" is marketing filler ("global SaaS company", "globally distributed") and would
# promote a US-only posting to GLOBAL. "remote" is excluded for the same reason it is not a region.
_GLOBAL_SIGNALS = (
    "worldwide",
    "anywhere in the world",
    "hire from anywhere",
    "work from anywhere",
    "open globally",
    "fully global",
)

# Structured restriction VALUES (exact place strings from a board) that mean "open to all". Safe
# to match exactly here because these are a board's own restriction field, not free prose.
_OPEN_PLACES = frozenset({"worldwide", "anywhere", "global", "international", "remote worldwide"})

_WORD_CACHE: dict[str, re.Pattern[str]] = {}


class EligibilityClassifier:
    """Classifies a posting against the seeker's profile into an EligibilityStatus + reason."""

    def __init__(self, profile: Profile) -> None:
        self._profile = profile
        self._rules = profile.eligibility
        self._home = profile.location.country.strip().lower()
        # The placeholder default is not a real country, so it must not match anything.
        self._home_is_real = bool(self._home) and self._home != "worldwide"
        # Normalized place strings for exact structured matching (see _structured_location_verdict).
        self._home_place = _normalize_place(self._home)
        self._region_places = {_normalize_place(r) for r in self._rules.eligible_regions}

    def classify(self, job: Job) -> Eligibility:
        timezone = self._timezone_verdict(job)
        if timezone is not None:
            return timezone
        location = self._structured_location_verdict(job)
        if location is not None:
            return location
        return self._text_verdict(job)

    def _timezone_verdict(self, job: Job) -> Eligibility | None:
        """A reported, restrictive timezone the seeker cannot overlap excludes the role outright.

        None here means "timezone does not decide", not "eligible": location and text still run.
        """
        offsets = job.hints.timezone_restrictions
        limit = self._rules.max_timezone_distance_hours
        if not offsets or limit is None:  # None (unreported) or () (unrestricted) or no rule
            return None
        home = self._profile.location.timezone_utc_offset
        if any(abs(offset - home) <= limit for offset in offsets):
            return None
        return Eligibility(
            status=EligibilityStatus.EXCLUDED_TIMEZONE,
            reason=f"requires a timezone more than {limit}h from your own (UTC{home:+g})",
        )

    def _structured_location_verdict(self, job: Job) -> Eligibility | None:
        """Decide from the board's stated location restrictions, or None to defer to the text.

        Restrictions are place strings (country names), so they are matched by EXACT normalized
        equality, not substring: "New Mexico" is a US state, not the country "Mexico", and
        substring matching told a Mexico-based seeker he could hold a US-only job. The known cost
        is that a broad profile region ("latam") will not match a specific country restriction
        ("Brazil") without a country-to-region map; that under-includes, which is the safe error.
        """
        restrictions = job.hints.location_restrictions
        if restrictions is None:  # the board said nothing about location
            return None
        if not restrictions:  # said: no restriction
            return Eligibility(
                status=EligibilityStatus.GLOBAL, reason="open to applicants anywhere"
            )
        stated = [_normalize_place(r) for r in restrictions]
        if any(place in _OPEN_PLACES for place in stated):
            return Eligibility(status=EligibilityStatus.GLOBAL, reason="open worldwide")
        if self._home_is_real and self._home_place in stated:
            return Eligibility(
                status=EligibilityStatus.HOME_BASED,
                reason=f"open in your country ({', '.join(restrictions)})",
            )
        if self._region_places & set(stated):
            return Eligibility(
                status=EligibilityStatus.REGIONAL,
                reason=f"open in your region ({', '.join(restrictions)})",
            )
        return Eligibility(
            status=EligibilityStatus.EXCLUDED_LOCATION,
            reason=f"restricted to {', '.join(restrictions)}, which does not include you",
        )

    def _text_verdict(self, job: Job) -> Eligibility:
        """No structured data: read the posting text against the profile's term lists.

        Matched by whole word, not substring, so a region "us" does not fire on "houston" and a
        lock "us only" does not fire on unrelated prose.
        """
        text = job.search_text
        if _any_mention(self._rules.disqualifying_authorization_terms, text):
            return Eligibility(
                status=EligibilityStatus.EXCLUDED_AUTHORIZATION,
                reason="demands work authorization you do not hold",
            )
        region_mentioned = _any_mention(self._rules.eligible_regions, text)
        if _any_mention(self._rules.location_lock_terms, text) and not region_mentioned:
            return Eligibility(
                status=EligibilityStatus.EXCLUDED_LOCATION,
                reason="locked to a location that excludes you",
            )
        if _any_mention(_GLOBAL_SIGNALS, text):
            return Eligibility(
                status=EligibilityStatus.GLOBAL, reason="states it hires from anywhere"
            )
        if self._home_is_real and _mentions(self._home, text):
            return Eligibility(status=EligibilityStatus.HOME_BASED, reason="mentions your country")
        if region_mentioned:
            return Eligibility(status=EligibilityStatus.REGIONAL, reason="mentions your region")
        return Eligibility(
            status=EligibilityStatus.REMOTE_UNVERIFIED,
            reason="remote, but eligibility could not be confirmed",
        )


_PLACE_PUNCT = re.compile(r"[^\w\s]")


def _normalize_place(text: str) -> str:
    """A place or region string, normalized for exact comparison: lower, unpunctuated, single-spaced."""
    return " ".join(_PLACE_PUNCT.sub(" ", text.casefold()).split())


def _mentions(term: str, text: str) -> bool:
    """True if `term` appears as a whole word/phrase in `text`. Both are already lower-cased."""
    pattern = _WORD_CACHE.get(term)
    if pattern is None:
        pattern = _WORD_CACHE[term] = re.compile(rf"\b{re.escape(term)}\b")
    return pattern.search(text) is not None


def _any_mention(terms: list[str] | tuple[str, ...], text: str) -> bool:
    return any(_mentions(term, text) for term in terms)

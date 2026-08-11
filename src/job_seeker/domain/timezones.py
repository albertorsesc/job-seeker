"""Named timezone bands, for boards that state one where a place is expected.

Remotive writes "USA timezones" and "European timezones" into the same comma-separated field it
writes "Germany" and "Europe" into. Read as a place, a band matches no country and pushes the
posting toward `excluded-location`, which is both the wrong reason and, for a band the seeker
actually sits in, the wrong answer: a seeker at UTC-6 meets "USA timezones" and was being told the
role was restricted away from them.

The engine already has the right home for this. `EligibilityHints.timezone_restrictions` carries
UTC offsets and `EligibilityRules.max_timezone_distance_hours` reads them, so a band only has to
become the offsets it covers and the existing rule decides.

Standard-time offsets, not daylight ones. A band shifts by an hour for part of the year, which is
inside any tolerance a seeker would set: the rule asks how far a seeker is from the nearest offset
in the band, and an hour of drift does not change that answer for anyone it was close for.

Deliberately small. Only the bands boards have actually been observed to state are here, because a
band this map does not know stays a place, which excludes, and inventing a taxonomy of bands nobody
publishes would be guessing about who may hold a job.
"""

from __future__ import annotations

import re

from job_seeker.domain.regions import canonical_place

# What makes a value a band rather than a place. Boards write "timezones", "timezone" and
# "time zones", so the space is optional and the plural is not required.
_BAND = re.compile(r"\btime\s?zones?\b")

# The place a band is named after -> the UTC offsets it covers, keyed canonically so "USA
# timezones" and "United States timezones" are the same band.
_OFFSETS: dict[str, tuple[float, ...]] = {
    # Eastern, Central, Mountain, Pacific. Alaska and Hawaii are deliberately absent: a role
    # advertised for "USA timezones" means the working day of the contiguous states.
    "united states": (-5.0, -6.0, -7.0, -8.0),
    # The same band under the short name. "us" is resolved here rather than as a place spelling
    # because a spelling is also what the text path searches posting prose for, where "us" is the
    # pronoun and "join us today" would read as the seeker's own country. Inside "<place>
    # timezones" there is no such ambiguity.
    "us": (-5.0, -6.0, -7.0, -8.0),
    # Western, Central and Eastern European time.
    "europe": (0.0, 1.0, 2.0),
}


def offsets_for_band(text: str) -> tuple[float, ...] | None:
    """The UTC offsets a named band covers, or None when this is not a band this map knows.

    None covers two different values on purpose, because the caller treats them the same way: a
    plain place ("Germany"), and a band nobody has taught this map ("Asian timezones"). Both stay
    whatever the caller already had them as, which for a restriction field means they keep
    excluding. An unreadable restriction that excludes is recoverable; one that goes quiet gets
    read as "the board said nothing" and promotes a restricted posting.
    """
    if not _BAND.search(text):
        return None
    return _OFFSETS.get(canonical_place(_BAND.sub(" ", text)))

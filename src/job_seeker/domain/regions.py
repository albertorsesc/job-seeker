"""A country-to-region map, so eligibility can reason about geography.

Job boards state a restriction as a specific country ("Brazil"), while a seeker states a profile
region ("latam"). Without a map, exact matching excludes a Brazil-restricted job from a LATAM
seeker, which is the one place the eligibility filter errs the wrong way (excluding a job the seeker
could hold). This is universal geography, not candidate data, so it is a built-in reference table
like the global-signal words, and a seeker who needs finer control can always list countries
directly in their profile.

This is a pragmatic starter set covering the regions job boards commonly use, not an exhaustive
gazetteer. All names are lower-cased to match the normalized restriction strings. Extending it is
adding a country to a set.

**A region is an authorization claim, not just geography.** A seeker who lists a region in their
profile is asserting they can legally work anywhere in it, so the map expands it to every member
country. That makes broad regions powerful and sharp: "americas" and "north america" include the
United States and Canada, and "emea" includes the Middle East and Africa. A seeker who cannot work
in the United States should list "latam" (or specific countries), not "americas", or the engine
will surface US-only jobs they cannot actually hold. The engine cannot infer work authorization
from geography; it trusts the profile to state it.
"""

from __future__ import annotations

_LATAM = frozenset(
    {
        "mexico",
        "brazil",
        "argentina",
        "chile",
        "colombia",
        "peru",
        "uruguay",
        "ecuador",
        "bolivia",
        "paraguay",
        "venezuela",
        "costa rica",
        "panama",
        "guatemala",
        "honduras",
        "nicaragua",
        "el salvador",
        "dominican republic",
    }
)
_NORTH_AMERICA = frozenset({"united states", "usa", "canada", "mexico"})
_EUROPE = frozenset(
    {
        "portugal",
        "spain",
        "france",
        "germany",
        "italy",
        "netherlands",
        "belgium",
        "ireland",
        "united kingdom",
        "uk",
        "poland",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "austria",
        "switzerland",
        "czechia",
        "czech republic",
        "romania",
        "greece",
        "hungary",
        "bulgaria",
        "croatia",
        "estonia",
        "latvia",
        "lithuania",
        "slovakia",
        "slovenia",
        "ukraine",
    }
)
_AFRICA = frozenset(
    {"nigeria", "kenya", "south africa", "egypt", "ghana", "morocco", "tunisia", "uganda"}
)
_MIDDLE_EAST = frozenset(
    {"israel", "turkey", "united arab emirates", "uae", "saudi arabia", "qatar", "jordan"}
)
_APAC = frozenset(
    {
        "india",
        "australia",
        "new zealand",
        "japan",
        "singapore",
        "philippines",
        "indonesia",
        "vietnam",
        "thailand",
        "malaysia",
        "south korea",
        "china",
        "hong kong",
        "taiwan",
    }
)
_AMERICAS = _LATAM | _NORTH_AMERICA
_EMEA = _EUROPE | _MIDDLE_EAST | _AFRICA

# Region name (and its aliases) -> member countries. Aliases share one country set.
REGION_MEMBERS: dict[str, frozenset[str]] = {
    "latam": _LATAM,
    "latin america": _LATAM,
    "north america": _NORTH_AMERICA,
    "americas": _AMERICAS,
    "south america": _LATAM,
    "europe": _EUROPE,
    "emea": _EMEA,
    "middle east": _MIDDLE_EAST,
    "africa": _AFRICA,
    "apac": _APAC,
    "asia pacific": _APAC,
    "asia": _APAC,
    "oceania": frozenset({"australia", "new zealand"}),
}


def expand_place(place: str) -> set[str]:
    """A place plus, if it is a known region, its member countries.

    A country expands to just itself, so intersecting two expanded places answers "could a seeker
    who accepts A hold a job restricted to B" in both directions: a profile region against a country
    restriction, and a country home against a region restriction.
    """
    return {place} | set(REGION_MEMBERS.get(place, frozenset()))

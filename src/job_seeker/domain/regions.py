"""Where places are, what they are called, and whose right to work they take.

Three questions the eligibility rules ask about every place a board names, answered here so they
are answered the same way everywhere:

- **Which places make up a region** (`REGION_MEMBERS`). Boards state a restriction as a specific
  country ("Brazil") while a seeker states a region ("latam"). Without a map, exact matching
  excludes a Brazil-restricted job from a LATAM seeker, which is the one direction this filter must
  not err in: excluding a job the seeker could hold.
- **Which names mean the same place** (`canonical_place`). Boards do not share a vocabulary, and
  neither does the hand-written profile, so one country arrives under several names.
- **Whose work authorization a place takes** (the territories table). A place can sit in one region
  and be held only by people with another country's right to work.

Universal reference data, not candidate data, so it is built in like the global-signal words, and
a seeker who needs finer control can always list countries directly in their profile. A pragmatic
starter set covering what boards actually send, not an exhaustive gazetteer: extending it is adding
a country to a set.

**A region is an authorization claim, not just geography.** A seeker who lists a region in their
profile is asserting they can legally work anywhere in it, so the map expands it to every member
country. That makes broad regions powerful and sharp: "americas" and "north america" include the
United States and Canada, and "emea" includes the Middle East and Africa. A seeker who cannot work
in the United States should list "latam" (or specific countries), not "americas", or the engine
will surface US-only jobs they cannot actually hold. The engine cannot infer work authorization
from geography; it trusts the profile to state it.
"""

from __future__ import annotations

import re
import unicodedata

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
        "belize",
        "jamaica",
        "haiti",
        # The Caribbean, which Himalayas restricts postings to by name.
        "cuba",
        "bahamas",
        "barbados",
        "dominica",
        "grenada",
        "trinidad and tobago",
        "saint lucia",
        "saint vincent and the grenadines",
        "antigua and barbuda",
        "saint kitts and nevis",
        "aruba",
        "curacao",
        "anguilla",
        "bermuda",
        "cayman islands",
        "montserrat",
        "guadeloupe",
        "martinique",
        "suriname",
        "guyana",
    }
)
_NORTH_AMERICA = frozenset({"united states", "canada", "mexico"})
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
        "poland",
        "sweden",
        "norway",
        "denmark",
        "finland",
        "austria",
        "switzerland",
        "czechia",
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
        "albania",
        "andorra",
        "bosnia and herzegovina",
        "cyprus",
        "luxembourg",
        "malta",
        "moldova",
        "montenegro",
        "north macedonia",
        "serbia",
    }
)
_AFRICA = frozenset(
    {"nigeria", "kenya", "south africa", "egypt", "ghana", "morocco", "tunisia", "uganda"}
)
_MIDDLE_EAST = frozenset(
    {"israel", "turkey", "united arab emirates", "saudi arabia", "qatar", "jordan", "kuwait"}
)
_OCEANIA = frozenset({"australia", "new zealand", "papua new guinea"})
# Asia Pacific covers Oceania, so it is composed from it rather than restating its members.
_APAC = _OCEANIA | frozenset(
    {
        "india",
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

# Several names for one place, so two boards naming it compare equal. Boards do not agree on a
# vocabulary: Himalayas writes common names ("United States"), WeWorkRemotely writes ISO 3166
# official names ("United States of America"), Remotive writes UN region names ("Northern
# America"), and profiles are written by hand. Read as distinct places, a seeker is told they
# cannot hold a job at home.
#
# **Admission rule: an entry here means the two names denote the same place.** Nothing else.
# A place that is somewhere else but shares its work authorization belongs in the table below,
# and a claim that one place ought to belong to another is not a spelling and does not go in
# either. Keys are stored normalized, which `test_regions` enforces: `canonical_place` normalizes
# before it looks up, so a key carrying punctuation would never be found.
_SPELLINGS: dict[str, str] = {
    "united states of america": "united states",
    "usa": "united states",
    "united kingdom of great britain and northern ireland": "united kingdom",
    "uk": "united kingdom",
    "korea republic of": "south korea",
    "moldova republic of": "moldova",
    "bolivia plurinational state of": "bolivia",
    "viet nam": "vietnam",
    "czech republic": "czechia",
    "uae": "united arab emirates",
    "northern america": "north america",
    "european": "europe",
}

# Places whose work authorization is another country's.
#
# **Admission rule: holding a role there takes that country's right to work.** This map answers
# "may the seeker hold it", so a territory follows the authorization rather than the geography,
# and the two can point opposite ways: Puerto Rico sits among its LATAM neighbours and takes US
# work authorization, so a LATAM seeker is correctly excluded from a Puerto Rico posting and a US
# seeker is correctly offered one.
_AUTHORIZATION_TERRITORIES: dict[str, str] = {
    "puerto rico": "united states",
    "guam": "united states",
    "us virgin islands": "united states",
}

_CANONICAL: dict[str, str] = {**_SPELLINGS, **_AUTHORIZATION_TERRITORIES}

# Anything that is not a word or a space. Stripped before comparison, so "Bolivia (Plurinational
# State of)" and "Cote d'Ivoire" compare as the places they are rather than as their punctuation.
_PLACE_PUNCT = re.compile(r"[^\w\s]")

# Canonical name -> every spelling of it, the inverse of `_SPELLINGS`. Built here rather than at
# each call so the two cannot drift.
_SPELLINGS_BY_CANONICAL: dict[str, frozenset[str]] = {}
for _spelling, _canonical in _SPELLINGS.items():
    _SPELLINGS_BY_CANONICAL[_canonical] = frozenset(
        _SPELLINGS_BY_CANONICAL.get(_canonical, frozenset()) | {_spelling, _canonical}
    )

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
    "oceania": _OCEANIA,
}


def normalize_place(text: str) -> str:
    """A place name reduced to the form places are compared in: lower, unaccented, unpunctuated,
    single-spaced.

    Accents come off because the same place is written both ways by the people and the boards that
    care about it most: a seeker in Mexico may write "México" in their own profile, Himalayas
    restricts postings to "Curaçao", and a board elsewhere writes both without the mark. Held as
    distinct strings, a seeker's own country stops matching itself.

    Comparison only. It answers "are these the same characters", not "are these the same place",
    which is `canonical_place`.
    """
    unaccented = "".join(
        character
        for character in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(character)
    )
    return " ".join(_PLACE_PUNCT.sub(" ", unaccented).split())


def canonical_place(text: str) -> str:
    """The one name this map reasons about a place under, whatever the caller called it.

    **The single entry point.** Normalizing and aliasing are one step on purpose: as two steps
    they were performed by two modules in two orders, and the aliases whose names carry
    punctuation ("Korea (Republic of)") were looked up in a table that had already had its own
    punctuation stripped, so those entries could never match anything. One function cannot
    disagree with itself about the order.

    A name the map has never heard of passes through normalized, so an unknown country still
    compares equal to itself and to another board's spelling of it.
    """
    normalized = normalize_place(text)
    return _CANONICAL.get(normalized, normalized)


def spellings_of(place: str) -> frozenset[str]:
    """Every name that means this place, including its own. For matching prose, where a posting
    writes the country however it likes and there is nothing to canonicalize.

    Spellings only, not territories: prose naming the United States is not naming Puerto Rico.
    The structured path can afford that inference because a board's restriction field is a precise
    claim; reading it into free text would manufacture one.
    """
    return _SPELLINGS_BY_CANONICAL.get(canonical_place(place), frozenset({canonical_place(place)}))


def expand_place(place: str) -> set[str]:
    """A place plus, if it is a known region, its member countries.

    A country expands to just itself, so intersecting two expanded places answers "could a seeker
    who accepts A hold a job restricted to B" in both directions: a profile region against a country
    restriction, and a country home against a region restriction.

    Canonical first, so the two sides of that intersection are in one vocabulary however the board
    and the profile each spelled their country.
    """
    place = canonical_place(place)
    return {place} | set(REGION_MEMBERS.get(place, frozenset()))

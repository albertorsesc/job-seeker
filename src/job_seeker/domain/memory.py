"""What the engine remembers between runs, as the domain understands it.

The seeker runs a search perhaps weekly. Without memory every run re-reads the same postings and
returns the same list, so there is no answer to "what changed since last time" and no way to say
"stop showing me this one". These are the types that answer both, and they cross the
`PostingMemory` port on their way in and out.

They describe *what is remembered*, never *how it is stored*. No path, no format, no file. A JSONL
adapter satisfies the port today; the domain would not notice if it became something else.

Imports nothing of ours, like every other module in `domain/`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class PostingDecision(StrEnum):
    """What the seeker decided about a posting. Two verdicts, both acted on by the search.

    Deliberately not an application tracker. `interviewing`, `rejected` and `offer` are states of a
    conversation with a company, not facts about a posting, and the engine cannot observe any of
    them. Two verdicts is what a search can honour: hide this, and remember I went for this.
    """

    APPLIED = "applied"  # kept in results, badged, so the seeker sees what they already went for
    DISMISSED = "dismissed"  # hidden from results unless explicitly asked for


class Sighting(BaseModel):
    """One posting, delivered to the seeker by this run.

    What the engine writes down. Never carries a decision: a search records that it showed
    something, and only the seeker decides what that means. That separation is what stops a search
    running concurrently with a `mark` from clobbering it.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    title: str
    company: str
    source: str
    url: str


class PostingRecord(BaseModel):
    """One posting as memory holds it, from before this run.

    `first_seen_at` is the first time the engine *showed* the seeker this posting, not the first
    time a board carried it. The distinction is the whole design: a posting is new when it is new
    to the person, so a run can never quietly file something as old that it never put in front of
    them.
    """

    model_config = ConfigDict(frozen=True)

    key: str
    title: str = ""
    company: str = ""
    source: str = ""
    urls: tuple[str, ...] = ()
    first_seen_at: datetime
    last_seen_at: datetime
    times_seen: int = 0
    # Absent, not "" and not a third enum member, when the seeker has not decided. There is exactly
    # one way to spell undecided, the same rule `EligibilityHints` exists to enforce.
    decision: PostingDecision | None = None
    decided_at: datetime | None = None
    note: str = ""


class Recollection(BaseModel):
    """Everything memory could recall, and how well it could recall it.

    `available` is the load-bearing field. A store that could not be read must not look like a store
    that is empty: the first would make every posting appear new and would silently stop hiding the
    ones the seeker banned, while the second is an ordinary first run. So the reasoning asks whether
    memory could answer at all before it asks what it answered.
    """

    records: dict[str, PostingRecord] = Field(default_factory=dict)
    # False when the store could not be read, or when the seeker turned memory off for this run.
    # Kept apart from `enabled` because "broken" and "off by choice" call for different words.
    available: bool = False
    enabled: bool = True
    previous_run_at: datetime | None = None
    error: str = ""


class MemoryWrite(BaseModel):
    """What a write actually did.

    `unknown` carries back every reference that did not resolve. A mark is all or nothing: a
    partially applied batch leaves the seeker with no way to know which half landed, and a
    dismissal on the wrong posting is worse than a typo being reported.
    """

    written: int = 0
    decided: tuple[PostingRecord, ...] = ()
    unknown: tuple[str, ...] = ()
    error: str = ""


def posting_handle(identity: str, /) -> str:
    """The short, shell-safe spelling of an identity, for a seeker to paste at a terminal.

    An identity is human-readable on purpose ("acme|senior ai engineer"), which makes it a poor
    command-line argument: it carries spaces, a pipe, and whatever punctuation a company put in its
    own name. Derived on every read rather than stored, so the two can never disagree.
    """
    return "jk_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

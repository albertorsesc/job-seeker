"""Outbound ports: what the application needs the outside world to do for it.

Every third-party job provider reaches the application through one of these Protocols and
through nothing else. The application never learns that Himalayas paginates and caps a page
at 20, that RemoteOK puts legal boilerplate in row zero, that WeWorkRemotely is an RSS feed
whose title is "Company: Role", or that JobSpy scrapes Indeed. A board's quirks stop at its
adapter.

These are `typing.Protocol`, so conformance is structural: an adapter satisfies a port by
having the right shape and never imports or subclasses anything from here. The arrow points
inward even at the type level.

Not `runtime_checkable` on purpose. An isinstance check against a Protocol only verifies that
method *names* exist, ignoring signatures and return types, so it reads like a guarantee while
providing almost none. Conformance is checked statically by mypy, where it means something.

Parameters are positional-only (`/`). mypy deliberately ignores parameter *names* when checking
protocol conformance, so an adapter may legitimately spell its argument anything it likes. Were
these named, a caller writing `source.fetch(query=q)` would type-check and then raise TypeError
at runtime against a perfectly conforming adapter. The `/` makes the signature say what mypy
already enforces.
"""

from __future__ import annotations

from typing import Protocol

from job_seeker.domain.memory import (
    MemoryWrite,
    PostingDecision,
    Recollection,
    Sighting,
)
from job_seeker.domain.models import SearchQuery, SearchResult, SourceResult
from job_seeker.domain.profile import Profile


class JobSource(Protocol):
    """A single job provider. Fetches postings and normalizes them to canonical `Job`s."""

    @property
    def name(self) -> str:
        """Stable identifier, e.g. "himalayas". Selects the source and labels its coverage.

        Declared as a read-only property rather than `name: str`, and that is the *permissive*
        form, not the strict one. A protocol variable is settable, so `name: str` would demand an
        assignable attribute and reject any implementation whose name is read-only, which is what
        every fake in the test suite is: their name arrives as a constructor argument.

        Declared this way, both shapes conform, and each is right somewhere. A shipped adapter
        uses a plain class attribute, because its name is a constant, it is the registry key, and
        it is read off the class before any instance exists. A source whose name is instance state
        uses a property. `tests/application/test_ports.py` pins both, so neither can be "tidied"
        into the other.
        """
        ...

    def is_available(self) -> bool:
        """Whether this source can run at all.

        False when an optional dependency is missing or a credential is absent. Must not
        raise and must not perform I/O: this is what `job-seeker sources` calls to list what
        is usable, and it has to stay fast and work offline.
        """
        ...

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Fetch and normalize. **Must not raise.**

        A board that is down, rate-limiting, or has quietly changed its response shape is an
        expected outcome rather than an exception: sibling sources are in flight on other
        threads and a run must survive any one of them failing. Report it in
        `SourceResult.error` instead. An exception escaping here is a bug in the adapter, not
        a board having a bad day, which is what lets the orchestrator treat the two
        differently.
        """
        ...


class ProfileProvider(Protocol):
    """Supplies the seeker profile. The application never learns where it lives."""

    def load(self) -> Profile:
        """Return a validated profile.

        The one outbound port that *should* raise. Without a profile there is no definition
        of "suitable", so there is nothing to degrade into; a run that continued would be
        confidently meaningless. Fail loudly, naming the file and the offending field.
        """
        ...


class Reporter(Protocol):
    """Renders a finished run. Presentation only."""

    def render(self, result: SearchResult, /) -> str:
        """Render to a string. Must not filter, reorder or re-rank.

        Those decisions belong to the domain and already happened. A reporter that repeats
        them shows up as the JSON and the HTML disagreeing about what the run found.
        """
        ...


class PostingMemory(Protocol):
    """What the seeker has already been shown, and what they decided about it.

    A port, unlike the domain services, because it crosses the boundary: a file on this machine
    that outlives the process. The application asks it what is remembered and tells it what was
    delivered; nothing above this Protocol learns that a file exists.

    **No method may raise.** A search must survive a broken memory exactly as it survives a board
    being down, and for the same reason: the seeker asked for jobs, and losing the answer because
    a bookkeeping file was unreadable serves nobody. Failures are reported in the returned value.
    """

    def recall(self) -> Recollection:
        """Everything remembered, and how well it could be remembered.

        An unreadable store returns `available=False` with the reason, not an empty recollection.
        The difference matters: empty means a first run where everything is genuinely new, while
        unreadable means nothing can be said, and answering the first when the truth is the second
        stops the seeker's dismissals being honoured without any sign that it happened.
        """
        ...

    def record(self, sightings: tuple[Sighting, ...], /) -> MemoryWrite:
        """Persist that these postings were delivered to the seeker.

        Touches when a posting was seen and how often, never what the seeker decided about it.
        That is what lets a search running concurrently with a `mark` leave the mark intact.
        """
        ...

    def decide(
        self, refs: tuple[str, ...], decision: PostingDecision | None, note: str, /
    ) -> MemoryWrite:
        """Set, or clear with None, the seeker's decision for each reference.

        A reference is a handle, a raw identity key, or a URL the store has seen, so the seeker can
        paste whichever of the three is already in front of them. All or nothing: if any reference
        does not resolve, nothing is written and every unresolved one comes back in
        `MemoryWrite.unknown`. A half-applied batch leaves the seeker unable to tell which half
        landed, and a dismissal on the wrong posting is worse than a typo being reported.
        """
        ...

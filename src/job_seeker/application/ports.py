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

from job_seeker.domain.models import SearchQuery, SearchResult, SourceResult
from job_seeker.domain.profile import Profile


class JobSource(Protocol):
    """A single job provider. Fetches postings and normalizes them to canonical `Job`s."""

    @property
    def name(self) -> str:
        """Stable identifier, e.g. "himalayas". Selects the source and labels its coverage."""
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

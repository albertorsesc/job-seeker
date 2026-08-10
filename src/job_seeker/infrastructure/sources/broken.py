"""A stand-in for a board whose factory raised.

A `SourceFactory` is contracted not to raise (see `registry.SourceFactory`), but that is a
docstring, not an enforcement, and a real adapter's constructor is where a missing credential or
an invalid config is discovered. When one breaks that contract, the run has to decide what a
board it cannot even build means.

It means the same thing as a board that is down. The engine already has an answer for that: report
it in `SourceCoverage` and carry on, so `SearchResult.is_complete` turns False and the seeker is
told the run was partial. `job-seeker sources` already treats a raising factory this way, via
`registry.describe()`. Substituting this source is what makes `find` agree with `sources` instead
of ending the whole search over one adapter.

`name` is a read-only property rather than a class attribute, unlike every shipped board: the name
is different per instance, because it is the name of whichever board failed. That is the exact case
`JobSource.name` is declared as a property to permit.
"""

from __future__ import annotations

from job_seeker.domain.models import SearchQuery, SourceResult


class BrokenSource:
    """Reports a construction failure as a failed `SourceResult` instead of an exception."""

    def __init__(self, name: str, error: str) -> None:
        self._name = name
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        """False: it could not be built, so it cannot run."""
        return False

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """The failure, in the shape every other source reports a failure in."""
        return SourceResult(source=self._name, error=self._error)

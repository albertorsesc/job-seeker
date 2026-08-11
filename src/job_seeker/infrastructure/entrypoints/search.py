"""The shared run wiring both entrypoints call.

The CLI and the MCP server each resolve their own profile and query, then hand them here to build
the sources and run the pipeline. Keeping the sources-and-run step in one place is deliberate: the
CLI and MCP once drifted on how they enumerate sources, and this is the seam where the same could
happen for search. One function, one behaviour.
"""

from __future__ import annotations

from job_seeker.application.orchestrator import JobSeeker
from job_seeker.application.ports import JobSource
from job_seeker.domain.models import SearchQuery, SearchResult
from job_seeker.domain.profile import Profile
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.broken import BrokenSource


def execute_search(
    profile: Profile, query: SearchQuery, source_names: list[str] | None
) -> SearchResult:
    """Select the registered sources (or the named subset) and run the search.

    `source_names=None` means every registered source. A named source that does not exist raises,
    rather than silently searching a subset, because a typo in `--sources` should be told, not
    honored as "search fewer boards".

    Registration is the composition root's job, not this function's. `cli.main` and
    `build_server` each call `register_builtins()` at startup, on the main thread, before anything
    can reach here, which is the invariant `registry` documents. Calling it here as well made a
    library function mutate process-global state on every search, and put a write to the registry
    on whatever thread the caller happened to be on. An embedder who reaches this with an empty
    registry gets the explicit error below, which is a better answer than a silent side effect.
    """
    sources = _select_sources(source_names)
    return JobSeeker.default(sources, profile).run(query)


def _select_sources(source_names: list[str] | None) -> list[JobSource]:
    available = registry.names()
    if not available:
        raise ValueError(
            "No job boards are registered. This build ships no source adapters, or none loaded."
        )
    names = source_names if source_names is not None else available
    unknown = [name for name in names if name not in available]
    if unknown:
        raise ValueError(
            f"unknown source(s): {', '.join(unknown)}. Registered: {', '.join(available)}."
        )
    return [_build(name) for name in names]


def _build(name: str) -> JobSource:
    """Construct a registered board, or a stand-in that reports why it could not be built.

    The two failures here are different and must not collapse. An unknown *name* is a typo and is
    refused above, because searching fewer boards than asked is not an answer. A registered board
    whose *factory* raises is an adapter bug, and the run survives it the same way it survives a
    board being down: as coverage. Ending the search instead would contradict `job-seeker sources`,
    which reports exactly this failure and keeps going.

    Catching `Exception` is deliberate and mirrors `registry.describe()` and the orchestrator's
    guard around `fetch`: a broken adapter may raise anything at all, and this is a seam whose job
    is to keep one board's bug from becoming the whole run's.
    """
    try:
        return registry.create(name)
    except Exception as exc:  # noqa: BLE001 - an adapter's constructor may raise anything
        return BrokenSource(name, f"{type(exc).__name__}: {exc}")

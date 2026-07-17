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
from job_seeker.infrastructure.sources.defaults import register_builtins


def execute_search(
    profile: Profile, query: SearchQuery, source_names: list[str] | None
) -> SearchResult:
    """Wire the built-in sources (or the named subset) and run the search.

    `source_names=None` means every registered source. A named source that does not exist raises,
    rather than silently searching a subset, because a typo in `--sources` should be told, not
    honored as "search fewer boards".
    """
    register_builtins()
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
    return [registry.create(name) for name in names]

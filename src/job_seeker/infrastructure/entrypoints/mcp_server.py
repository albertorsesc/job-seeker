"""The MCP entrypoint: the other half of the composition root.

Exposes the engine to a local agent over stdio, so a scan runs on the seeker's own machine and
never has to be delegated to a hosted assistant.

The SDK is imported lazily, inside the functions that need it. `mcp` is an optional extra, and
this module is the target of the `job-seeker-mcp` console script, so a module-level import would
turn "installed without the mcp extra" into an ImportError traceback at startup. Under
`from __future__ import annotations` the type annotations are strings, so the TYPE_CHECKING
import costs nothing at runtime.
"""

from __future__ import annotations

import importlib.util
import sys
from typing import TYPE_CHECKING, Any

from job_seeker import __version__
from job_seeker.domain.models import SearchQuery
from job_seeker.infrastructure.config.profile_loader import MarkdownProfileProvider
from job_seeker.infrastructure.entrypoints.search import execute_search
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_MISSING_SDK = (
    "The MCP server needs the optional 'mcp' extra, which is not installed.\n"
    'Install it with:  uv pip install "job-seeker[mcp]"'
)


def build_server() -> FastMCP:
    """Construct the server and register its tools.

    Separate from `main` so the tools can be exercised in tests. `run()` blocks on stdio
    forever, so anything that calls it is untestable by construction.
    """
    from mcp.server.fastmcp import FastMCP

    register_builtins()  # composition root: wire the built-in adapters to the registry

    server = FastMCP("job-seeker")

    # FastMCP accepts no `version` argument as of SDK 1.28.1, and passes none to the underlying
    # server, whose fallback reports the *SDK's* version. Left alone, every client, log and bug
    # report sees "1.28.1" where job-seeker's own version belongs, and no release ever matches.
    # The private attribute is the only route today; a test pins it so an SDK change is loud.
    server._mcp_server.version = __version__

    @server.tool()
    def list_sources() -> list[dict[str, Any]]:
        """List the job boards this engine can search, and whether each one can run now.

        A board reports unavailable when an optional dependency or credential is missing. It is
        still listed, because "this board exists but cannot run" and "this board does not exist"
        are different answers and the agent should be able to tell the seeker which it is.

        Uses `describe()`, the same failure-isolating path the CLI's `sources` command uses. A
        board whose constructor or availability check raises must not blind the agent to the
        boards that work, and the two entrypoints must not disagree about that.
        """
        return [
            {"name": status.name, "available": status.available, "error": status.error}
            for status in registry.describe()
        ]

    @server.tool()
    def describe_engine() -> dict[str, Any]:
        """Report what this engine is and what it can currently do."""
        return {
            "version": __version__,
            "registered_sources": registry.names(),
            "can_search": True,
            "note": "Call find_jobs to search. It reads the profile from JOB_SEEKER_PROFILE.",
        }

    @server.tool()
    def find_jobs(
        terms: list[str] | None = None,
        limit: int = 50,
        max_age_days: int = 30,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search the job boards and return the postings the seeker can actually hold, ranked.

        The seeker's profile is read from the JOB_SEEKER_PROFILE environment variable; it is the
        profile, not the caller, that decides what "suitable" means. `terms` overrides the
        profile's default search terms. Each result carries a fit score and an eligibility verdict
        with a reason. The `coverage` and `is_complete` fields say how much of each board was
        scanned, so a partial run (a board down, a scan truncated) is never mistaken for a
        thorough one.
        """
        profile = MarkdownProfileProvider.from_env().load()
        # terms fall back to the profile's; if both are empty the relevance filter simply does not
        # narrow, returning every eligible job. No invented default term, which would be one
        # person's search baked into a reusable engine.
        query = SearchQuery(
            terms=terms or profile.search_terms,
            max_results_per_source=limit,
            max_age_days=max_age_days,
        )
        result = execute_search(profile, query, sources)
        return result.model_dump(mode="json")

    return server


def main() -> int:
    # `find_spec`, not `except ImportError`. Catching the exception conflates "the extra is not
    # installed" with "the SDK is installed but its own imports are broken", and answers the
    # second with install advice pip will report as already satisfied, having swallowed the real
    # traceback. find_spec asks the question actually meant, and does not execute the package.
    if importlib.util.find_spec("mcp") is None:
        print(_MISSING_SDK, file=sys.stderr)
        return 2
    build_server().run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

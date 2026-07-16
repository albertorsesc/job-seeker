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
        """Report what this engine is and what it can currently do.

        Exists so an agent can discover that `find_jobs` is not available yet rather than
        calling it and interpreting the failure. job-seeker is early in development.
        """
        return {
            "version": __version__,
            "registered_sources": registry.names(),
            "can_search": False,
            "note": (
                "Board adapters, scoring and eligibility are still being built. No search tool "
                "is exposed yet, because returning an empty result would be indistinguishable "
                "from a search that found nothing."
            ),
        }

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

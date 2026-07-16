"""The command line entrypoint, and one half of the composition root.

Translates an argv into a call and a result back into text. It holds no business logic: a rule
that lives here is a rule the MCP server will disagree with.

`find` is not implemented yet and says so, loudly, with a non-zero exit. That is the point. A
command that quietly returned an empty list would be indistinguishable from a search that found
nothing, which is the worst possible failure for a tool whose entire job is telling you what is
out there.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from job_seeker import __version__
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-seeker",
        description="Find the jobs you can actually hold, ranked against your profile.",
    )
    parser.add_argument("--version", action="version", version=f"job-seeker {__version__}")

    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("sources", help="list the job boards and whether each one can run")
    commands.add_parser("find", help="search the boards and rank what you can hold")
    return parser


def _sources() -> int:
    """List every registered board and whether it can run right now.

    Uses `describe()`, which isolates per-board failure. This is the command you run because
    something is broken, so one broken adapter must not hide the boards that work.
    """
    statuses = registry.describe()
    if not statuses:
        print("No job boards are registered yet.")
        print("job-seeker is early in development: the board adapters are not written.")
        return 0

    width = max(len(status.name) for status in statuses)
    for status in statuses:
        if status.error:
            state = f"broken       {status.error}"
        else:
            state = "available" if status.available else "unavailable"
        print(f"{status.name:<{width}}  {state}")
    return 0


def _find() -> int:
    """Refuse clearly. See the module docstring on why this must not return an empty list."""
    print("job-seeker find is not implemented yet.", file=sys.stderr)
    print(
        "The board adapters, scoring and eligibility rules are still being built. "
        "Run `job-seeker sources` to see what is registered.",
        file=sys.stderr,
    )
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    register_builtins()  # composition root: wire the built-in adapters to the registry

    if args.command == "sources":
        return _sources()
    if args.command == "find":
        return _find()

    # stderr, not stdout: this path returns a failure code, and `job-seeker | jq` getting a page
    # of help text on stdout is the same lie `_find` goes out of its way to avoid. Full help
    # rather than argparse's `required=True`, which prints bare usage and is less useful here.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

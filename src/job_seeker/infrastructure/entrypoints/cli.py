"""The command line entrypoint, and one half of the composition root.

Translates an argv into a call and a result back into text. It holds no business logic: a rule
that lives here is a rule the MCP server will disagree with, so the search itself runs through the
shared `execute_search` wiring, not code duplicated here.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from job_seeker import __version__
from job_seeker.domain.models import DEFAULT_SCAN_DEPTH, SearchQuery, SearchResult, SortOrder
from job_seeker.infrastructure.config.profile_loader import MarkdownProfileProvider, ProfileError
from job_seeker.infrastructure.entrypoints.bounds import describe_bounds_error
from job_seeker.infrastructure.entrypoints.search import execute_search
from job_seeker.infrastructure.reporting import FORMATS, reporter_for
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins

# `SearchQuery` owns the bounds, because the MCP tool accepts the same numbers and the two must
# not disagree about what is acceptable. So argparse does not re-declare them, and this maps a
# rejected field back to the flag the seeker typed: "scan_depth_per_source" is a name they never
# wrote and cannot act on.
_FIELD_FLAGS = {
    "scan_depth_per_source": "--scan-depth",
    "max_results": "--max-results",
    "max_age_days": "--max-age-days",
    "terms": "--terms",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="job-seeker",
        description="Find the jobs you can actually hold, ranked against your profile.",
    )
    parser.add_argument("--version", action="version", version=f"job-seeker {__version__}")

    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser("sources", help="list the job boards and whether each one can run")

    find = commands.add_parser("find", help="search the boards and rank what you can hold")
    find.add_argument("--profile", help="path to your profile file (default: $JOB_SEEKER_PROFILE)")
    find.add_argument("--terms", help="comma-separated search terms (default: the profile's)")
    find.add_argument(
        "--scan-depth",
        type=int,
        default=DEFAULT_SCAN_DEPTH,
        help=f"how many postings to read per board (default: {DEFAULT_SCAN_DEPTH}). Lowering this "
        "makes a search faster and its answer worse: it shrinks the pool a result is chosen from, "
        "and it does not shorten the output.",
    )
    find.add_argument(
        "--min-fit",
        type=float,
        default=0.0,
        help="drop postings below this fit, 0.0 to 1.0 (default: 0.0, keep everything). Fit is "
        "the share of your whole profile a posting matched, so a useful floor is lower than the "
        "word percent suggests.",
    )
    find.add_argument(
        "--max-results",
        type=int,
        help="cap the ranked output (default: no cap). Applied after ranking, so it keeps the best.",
    )
    find.add_argument(
        "--stated-only",
        action="store_true",
        help="only postings whose eligibility the board affirmatively stated, dropping the "
        "'remote-verify' ones nobody cleared. Narrows this search only.",
    )
    find.add_argument(
        "--sort",
        choices=[o.value for o in SortOrder],
        default=SortOrder.FIT.value,
        help="fit (default) ranks by how well a posting matches you; confidence groups by how "
        "sure the eligibility verdict is, then by fit inside each group.",
    )
    find.add_argument("--max-age-days", type=int, default=30, help="ignore older postings")
    find.add_argument("--sources", help="comma-separated source names (default: all)")
    find.add_argument("--format", choices=FORMATS, default="html", help="output format")
    find.add_argument("--out", help="write to this file instead of stdout")
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


def _find(args: argparse.Namespace) -> int:
    """Load the profile, run the search, render the report. Errors go to stderr with exit 2."""
    try:
        provider = (
            MarkdownProfileProvider(args.profile)
            if args.profile
            else MarkdownProfileProvider.from_env()
        )
        profile = provider.load()
    except ProfileError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    terms = _resolve_terms(args.terms, profile.search_terms)
    if not terms and not profile.role_include:
        # Nothing to narrow by: no terms and no role filter would return every eligible posting.
        # The relevance filter would allow that (and the MCP tool does), but the CLI asks for a
        # narrower search rather than dumping the whole eligible feed on a terminal.
        print(
            "No way to narrow the search: pass --terms, or set search_terms or role_include in "
            "your profile.",
            file=sys.stderr,
        )
        return 2

    try:
        query = SearchQuery(
            terms=terms,
            scan_depth_per_source=args.scan_depth,
            min_fit=args.min_fit,
            max_results=args.max_results,
            max_age_days=args.max_age_days,
            stated_only=args.stated_only,
            sort=SortOrder(args.sort),
        )
    except ValidationError as exc:
        print(describe_bounds_error(exc, _FIELD_FLAGS), file=sys.stderr)
        return 2

    source_names = _split(args.sources) or None  # None = every registered source
    try:
        result = execute_search(profile, query, source_names)
    except ValueError as exc:  # unknown source name, or none registered
        print(str(exc), file=sys.stderr)
        return 2

    notice = _run_notice(result)
    if notice:
        print(notice, file=sys.stderr)

    report = reporter_for(args.format).render(result)
    if args.out:
        try:
            Path(args.out).write_text(report, encoding="utf-8")
        except OSError as exc:
            print(f"Could not write to {args.out}: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote {len(result.jobs)} jobs to {args.out}", file=sys.stderr)
    else:
        print(report)
    return 0


def _run_notice(result: SearchResult) -> str:
    """What the run did not see, on stderr, whatever the format. "" when there is nothing to say.

    JSON and HTML carry coverage inside the report, but CSV is a flat table of jobs with nowhere to
    put it, so a board that failed there was a header row and silence. stderr tells the human
    without contaminating a stdout someone is piping into jq or a spreadsheet.

    Two messages, not one, and the wording is the point. A capped scan happens on every ordinary
    run, so phrasing it as a warning taught the reader to skip the line, and then they skip it on
    the day a board is down. A failure is announced; a cap is merely stated.
    """
    failed = [c.source for c in result.coverage if c.failed]
    if not result.all_sources_ran:
        # No coverage at all means no source ran, the most incomplete run there is, and the one
        # with nothing to list. Naming it beats an empty parenthesis.
        detail = ", ".join(failed) if failed else "no sources ran"
        return (
            f"WARNING: {len(failed)} of {len(result.coverage)} boards failed ({detail}). "
            f"These results are missing whatever those boards would have returned."
        )
    if not result.fully_scanned:
        capped = ", ".join(c.source for c in result.coverage if c.truncated)
        return (
            f"Note: scan capped at --scan-depth on {capped}; these are the best of what was read, "
            f"not of everything posted."
        )
    return ""


def _resolve_terms(flag: str | None, from_profile: list[str]) -> list[str]:
    return _split(flag) or from_profile


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()] if value else []


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    register_builtins()  # composition root: wire the built-in adapters to the registry

    if args.command == "sources":
        return _sources()
    if args.command == "find":
        return _find(args)

    # stderr, not stdout: this path returns a failure code, and `job-seeker | jq` getting a page
    # of help text on stdout is the same lie `_find` goes out of its way to avoid.
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

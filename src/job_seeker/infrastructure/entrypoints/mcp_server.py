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

from pydantic import ValidationError

from job_seeker import __version__
from job_seeker.domain.models import SearchQuery, SearchResult
from job_seeker.infrastructure.config.profile_loader import (
    MarkdownProfileProvider,
    ProfileError,
)
from job_seeker.infrastructure.entrypoints.bounds import describe_bounds_error
from job_seeker.infrastructure.entrypoints.search import execute_search
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins

# How `find_jobs` spells the arguments `SearchQuery` validates. The CLI keeps its own mapping to
# flags; the two differ on purpose, since an agent passes `limit` where a seeker types `--limit`.
_FIELD_PARAMS = {
    "scan_depth_per_source": "scan_depth",
    "max_results": "max_results",
    "max_age_days": "max_age_days",
    "terms": "terms",
}

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_MISSING_SDK = (
    "The MCP server needs the optional 'mcp' extra, which is not installed.\n"
    "Reinstall with it:\n"
    '  pip install "job-seeker[mcp] @ git+https://github.com/albertorsesc/job-seeker.git"'
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
        problem = _profile_problem()
        return {
            "version": __version__,
            "registered_sources": registry.names(),
            # Actually checked, not asserted. A hardcoded True made this tool blind to the single
            # most likely failure, which is the one an agent would call it to discover.
            "can_search": problem is None and bool(registry.names()),
            "profile_problem": problem or "",
            "note": "Call find_jobs to search. It reads the profile from JOB_SEEKER_PROFILE.",
        }

    @server.tool()
    def describe_profile() -> dict[str, Any]:
        """Report who the engine is searching as, so the seeker can check it before trusting a run.

        Every verdict this engine produces is a function of the profile: which roles count as
        relevant, which skills earn fit, and above all which postings the seeker is eligible to
        hold. A misconfigured profile does not error, it quietly answers for the wrong person, so
        state who that is before reporting results the seeker is meant to act on.

        Read `eligible_regions` back to the seeker when eligibility matters. A region here is an
        authorization claim, not geography: listing "americas" includes the United States, so a
        seeker without US work authorization would see US-only roles reported as holdable.
        """
        profile = MarkdownProfileProvider.from_env().load()
        rules = profile.eligibility
        return {
            "name": profile.name,
            "headline": profile.headline,
            "seniority": profile.seniority,
            "country": profile.location.country,
            "timezone_utc_offset": profile.location.timezone_utc_offset,
            "default_search_terms": profile.search_terms,
            "skills_weighted": profile.skills,
            "roles_wanted": profile.role_include,
            "roles_excluded": profile.role_exclude,
            "eligible_regions": rules.eligible_regions,
            "disqualifying_authorization_terms": rules.disqualifying_authorization_terms,
            "location_lock_terms": rules.location_lock_terms,
            "max_timezone_distance_hours": rules.max_timezone_distance_hours,
            "includes_unverified_postings": rules.include_unverified,
        }

    @server.tool()
    def find_jobs(
        terms: list[str] | None = None,
        scan_depth: int = 50,
        max_results: int | None = None,
        max_age_days: int = 30,
        sources: list[str] | None = None,
    ) -> SearchResult:
        """Search the job boards and return the postings the seeker can actually hold, ranked.

        The seeker's profile is read from the JOB_SEEKER_PROFILE environment variable; it is the
        profile, not the caller, that decides what "suitable" means. `terms` overrides the
        profile's default search terms. Each result carries a fit score and an eligibility verdict
        with a reason. Read `all_sources_ran` before reporting results: when it is false a board
        failed and whole categories of job are missing. `fully_scanned` is false whenever a board
        was read only to `scan_depth`, which is the ordinary case.

        `scan_depth` is how many postings to READ per board, not how many to return. Raising it
        widens the pool the answer is chosen from; use `max_results` to shorten the answer, which
        is applied after ranking so it keeps the best.

        To compare pay, use `salary.annual_minimum`/`annual_maximum`, never the raw figures: boards
        quote hourly and annual pay in the same field. Those annual figures are null when the board
        did not say what period it meant, which is common, so a question like "which pay over 150k"
        must say how many postings it could not judge rather than silently dropping them.

        Job descriptions are truncated in this payload; the full posting is at `job.url`.
        """
        profile = MarkdownProfileProvider.from_env().load()
        # terms fall back to the profile's; if both are empty the relevance filter simply does not
        # narrow, returning every eligible job. No invented default term, which would be one
        # person's search baked into a reusable engine.
        try:
            query = SearchQuery(
                terms=terms or profile.search_terms,
                scan_depth_per_source=scan_depth,
                max_results=max_results,
                max_age_days=max_age_days,
            )
        except ValidationError as exc:
            # Re-raised in the agent's own vocabulary. `SearchQuery` rejects
            # `max_results_per_source`, which is not a parameter this tool exposes, so an agent
            # reading the raw message cannot tell which argument to change or to what.
            raise ValueError(describe_bounds_error(exc, _FIELD_PARAMS)) from exc
        return _fit_for_context(execute_search(profile, query, sources))

    return server


# How much of a job description survives into an agent's context window.
#
# Measured, not guessed: a broad live search returned 11 postings as 58 KB, of which 48 KB was
# descriptions, and the MCP SDK sends the payload twice (once as text for the model, once as
# structured content), so that search cost roughly 29,000 tokens. Descriptions are what the engine
# reasons over, and it has already done that by the time this runs: scoring, eligibility and
# relevance all read the full text upstream. What reaches the agent only has to be enough to
# recognise the role, and the whole posting is one fetch away at `job.url`.
_DESCRIPTION_BUDGET = 600


def _fit_for_context(result: SearchResult) -> SearchResult:
    """The finished result with descriptions trimmed for an agent's context window.

    Trimming here, at the driving adapter, rather than in the pipeline: the full description is
    what the scorer and the eligibility classifier read, so shortening it upstream would change
    which jobs are found. This changes only what is reported.
    """
    return result.model_copy(
        update={
            "jobs": [
                scored.model_copy(
                    update={
                        "job": scored.job.model_copy(
                            update={"description": _trim(scored.job.description)}
                        )
                    }
                )
                for scored in result.jobs
            ]
        }
    )


def _trim(description: str) -> str:
    """Cut to the budget on a word boundary, saying so, or return it unchanged if it already fits."""
    if len(description) <= _DESCRIPTION_BUDGET:
        return description
    cut = description[:_DESCRIPTION_BUDGET].rsplit(" ", 1)[0]
    return f"{cut} [truncated, full posting at the job url]"


def _profile_problem() -> str | None:
    """Why a search would fail right now, or None if the profile loads.

    Loading is the check: an unset variable, a missing file and malformed YAML are all things a
    seeker hits, and all of them surface here as the message `find_jobs` would have raised, so an
    agent can report the cause instead of discovering it mid-search.
    """
    try:
        MarkdownProfileProvider.from_env().load()
    except ProfileError as exc:
        return str(exc)
    return None


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

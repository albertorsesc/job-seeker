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
from job_seeker.domain.memory import MemoryWrite, PostingDecision
from job_seeker.domain.models import DEFAULT_SCAN_DEPTH, SearchQuery, SearchResult, SortOrder
from job_seeker.infrastructure.config.profile_loader import (
    MarkdownProfileProvider,
    ProfileError,
)
from job_seeker.infrastructure.entrypoints.bounds import describe_bounds_error
from job_seeker.infrastructure.entrypoints.search import execute_search
from job_seeker.infrastructure.memory import JsonlPostingMemory
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins

# How `find_jobs` spells the arguments `SearchQuery` validates. The CLI keeps its own mapping to
# flags; the two differ on purpose, since an agent passes `limit` where a seeker types `--limit`.
_FIELD_PARAMS = {
    "scan_depth_per_source": "scan_depth",
    "min_fit": "min_fit",
    "new_only": "new_only",
    "include_dismissed": "include_dismissed",
    "max_results": "max_results",
    "max_age_days": "max_age_days",
    "terms": "terms",
}

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

# What the agent is told once, at connect, before it calls anything.
#
# Operating knowledge that spans tools has nowhere else to live: a per-tool description cannot say
# "call this one first", and the README is written for a human who is not in the loop when the
# agent decides what to do. Kept short because it ships on every session.
_INSTRUCTIONS = """\
This engine answers one question: which job postings can this specific person actually hold, and
how well do they fit. Every verdict is a function of their profile, not of the request.

Call `describe_profile` before reporting results the seeker will act on, and say whose profile you
used. A misconfigured profile does not fail, it answers confidently for the wrong person: a real
one silently claimed eligibility across the whole Americas and surfaced US-only roles to someone
who cannot work in the United States.

Read `eligibility.status` on every posting. It is the difference between a fact and a lead:
  home-based / regional / global   the board stated this person may hold it. Trust it.
  remote-verify                    nobody said. Worth checking, not worth asserting.
  excluded-*                       the board ruled them out. These are already filtered out.

A posting from a US company is not automatically out of reach, and one tagged for a country the
seeker cannot work in is not rescued by being remote. Only the status matters, never the company's
location or the word "remote" in the title.

Prefer `sort="confidence"` for "what can I hold" questions: it puts everything a board cleared
above everything nobody did, best fit first within each. Use `stated_only=True` only when the
seeker wants a shortlist to apply to, since it hides leads entirely.

Say when the answer is partial. `all_sources_ran=false` means a board failed and whole categories
of job are missing. `fully_scanned=false` is ordinary and means the scan hit `scan_depth`.

Results are a snapshot of a live, constantly-changing feed, not a reproducible query.

`history.is_new` means this run is the first time the engine has shown the seeker that posting, not
that the board posted it recently. `history` is absent when the journal could not be read, so say
memory was unreadable rather than guessing. Read `memory.healthy`: when it is false nothing is
marked new and postings the seeker dismissed are NOT hidden.

Call `mark_jobs` only when the seeker says they applied or wants a posting gone. Never infer it
from them opening a link or from your own read of the fit: it is their record of what they did, and
a wrong entry is invisible to them.
"""

_MISSING_SDK = (
    "The MCP server needs the optional 'mcp' extra, which is not installed.\n"
    "Reinstall with it:\n"
    '  pip install "job-seeker[mcp] @ git+https://github.com/albertorsesc/job-seeker.git"'
)


def build_server() -> MCPServer:
    """Construct the server and register its tools.

    Separate from `main` so the tools can be exercised in tests. `run()` blocks on stdio
    forever, so anything that calls it is untestable by construction.
    """
    from mcp.server.mcpserver import MCPServer

    register_builtins()  # composition root: wire the built-in adapters to the registry

    # The version is the engine's own, and it reaches the handshake every client reads. The SDK
    # takes it as a constructor argument, so nothing here has to reach past the public surface to
    # set it.
    server = MCPServer("job-seeker", version=__version__, instructions=_INSTRUCTIONS)

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
    def mark_jobs(refs: list[str], decision: PostingDecision, note: str = "") -> MemoryWrite:
        """Record that the seeker applied to these postings, or wants them gone for good.

        A `ref` is the `history.handle` from a search result, a raw `history.key`, or the posting's
        URL. Pass several to decide several at once.

        **Only call this when the seeker has told you they applied, or told you to drop something.**
        Never infer it from them opening a link, asking a question about a posting, or from your own
        judgement of how well it fits. This is their own record of what they did, and a wrong entry
        is invisible to them: a posting dismissed by mistake simply stops appearing, with nothing to
        notice. `unmark_jobs` reverses it.

        All or nothing. If any ref does not resolve, nothing is written and this raises naming the
        ones that did not, so you can never report "dismissed" for a ref you invented.
        """
        return _decide(refs, decision, note)

    @server.tool()
    def unmark_jobs(refs: list[str]) -> MemoryWrite:
        """Forget a decision, putting the posting back into ordinary results.

        A separate verb rather than passing null to `mark_jobs`: claiming something and retracting
        it are different acts, and a magic null is the wrong place to hide the difference.
        """
        return _decide(refs, None, "")

    def _decide(refs: list[str], decision: PostingDecision | None, note: str) -> MemoryWrite:
        written = JsonlPostingMemory.from_env().decide(tuple(refs), decision, note)
        if written.unknown:
            raise ValueError(
                "Nothing was written. These do not match any posting in the journal: "
                + ", ".join(written.unknown)
            )
        if written.error:
            raise ValueError(f"The journal could not be written: {written.error}")
        return written

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
        scan_depth: int = DEFAULT_SCAN_DEPTH,
        max_results: int | None = None,
        max_age_days: int = 30,
        min_fit: float = 0.0,
        new_only: bool = False,
        include_dismissed: bool = False,
        stated_only: bool = False,
        sort: SortOrder = SortOrder.FIT,
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

        `stated_only=True` drops postings whose eligibility nobody stated, leaving only those a
        board affirmatively cleared. Use it when the seeker asks what they can definitely hold;
        leave it off to see leads worth checking, which arrive as `remote-verify`.

        `sort="confidence"` puts every posting a board affirmatively cleared above every posting
        nobody did, best fit first within each group, so a cleared weak match outranks an uncleared
        strong one. Default `"fit"` ignores the distinction and ranks purely on match quality.

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
                min_fit=min_fit,
                new_only=new_only,
                include_dismissed=include_dismissed,
                max_results=max_results,
                max_age_days=max_age_days,
                stated_only=stated_only,
                sort=sort,
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

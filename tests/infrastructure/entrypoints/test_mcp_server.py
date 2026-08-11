"""Covers `job_seeker.infrastructure.entrypoints.mcp_server`.

`main()` is deliberately untested: it calls `server.run()`, which blocks on stdio forever. That
is exactly why `build_server()` exists separately. The end-to-end path is covered by
`storage/scripts/probe_mcp_stdio.py`, which drives the installed binary with a real client.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.fastmcp import FastMCP

from job_seeker import __version__
from job_seeker.domain.models import (
    Eligibility,
    EligibilityHints,
    EligibilityStatus,
    FitScore,
    Job,
    Relevance,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
    SourceResult,
)
from job_seeker.infrastructure.entrypoints import mcp_server
from job_seeker.infrastructure.sources import defaults, registry

from ..conftest import FakeSource


async def _structured(server: FastMCP, tool: str) -> dict[str, Any]:
    """Call a tool and return its structured payload.

    The cast documents an SDK discrepancy rather than papering over one: as of mcp 1.28.1
    `FastMCP.call_tool` is annotated `Sequence[ContentBlock] | dict[str, Any]` but actually
    returns a `(content, structured)` tuple. The annotation is wrong, not our usage, and mypy is
    right to object to indexing it. Pinned here in one place so an SDK fix shows up as one
    failure instead of six.
    """
    result = await server.call_tool(tool, {})
    return cast(tuple[Any, dict[str, Any]], result)[1]


def _write_profile(tmp_path: Path) -> Path:
    path = tmp_path / "p.md"
    path.write_text(
        "---\nlocation:\n  country: Testland\nsearch_terms: [Engineer]\n"
        "skills:\n  '\\bpython\\b': 3\n---\n"
    )
    return path


class TestFindJobsRejectsOutOfRangeArgumentsInTheAgentsOwnWords:
    """The agent writes `scan_depth`; the model rejects `scan_depth_per_source`.

    An agent handed "scan_depth_per_source: Input should be less than or equal to 1000" cannot act
    on it: that is not a parameter it passed, and `find_jobs` does not expose one by that name. The
    CLI already translates its rejections back to `--scan-depth`; the MCP surface is the public contract
    for agents and must not be the worse-served of the two.
    """

    @pytest.mark.parametrize(
        "arguments,expected",
        [
            pytest.param({"scan_depth": 100000}, "scan_depth", id="scan-depth-above-maximum"),
            pytest.param({"scan_depth": 0}, "scan_depth", id="scan-depth-below-minimum"),
            pytest.param({"max_age_days": 0}, "max_age_days", id="max-age-days-below-minimum"),
        ],
    )
    async def test_the_message_names_the_tool_parameter(
        self,
        arguments: dict[str, Any],
        expected: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(_write_profile(tmp_path)))
        server = mcp_server.build_server()
        with pytest.raises(Exception) as caught:
            await server.call_tool("find_jobs", {"terms": ["engineer"], **arguments})
        message = str(caught.value)
        assert expected in message
        assert "scan_depth_per_source" not in message or expected == "scan_depth_per_source"


class TestTheAgentIsToldWhatItWillReceive:
    """`find_jobs` returned `dict[str, Any]`, so `tools/list` published no output schema at all.

    An agent learned the payload shape by looking at one result and generalising, which is exactly
    the inference the engine exists to remove. Returning the model publishes the contract.
    """

    async def test_find_jobs_publishes_an_output_schema(self) -> None:
        tool = await self._find_jobs_tool()
        assert tool.outputSchema is not None
        assert tool.outputSchema.get("properties", {}).keys() >= {"jobs", "coverage", "query"}

    @pytest.mark.parametrize("field", ["salary", "annual_minimum", "eligibility", "fit"])
    async def test_the_schema_describes_the_fields_an_agent_reasons_with(self, field: str) -> None:
        assert field in json.dumps((await self._find_jobs_tool()).outputSchema)

    @pytest.mark.parametrize("derived", ["all_sources_ran", "annual_minimum", "is_eligible"])
    async def test_derived_fields_are_named_in_the_descriptions(self, derived: str) -> None:
        """They cannot be schema *properties*: the SDK builds the output schema in validation mode
        (`model_json_schema(schema_generator=StrictJsonSchema)`, no `mode=`), and pydantic omits
        computed fields there. They do arrive in the payload, so each model's description says so
        rather than leaving the agent to discover them.
        """
        assert derived in json.dumps((await self._find_jobs_tool()).outputSchema)

    async def test_the_schema_does_not_carry_maintainer_rationale(self) -> None:
        """The description text ships to every agent on every session, so it explains the payload
        rather than the project's history. These phrases were in it and are now in comments."""
        schema = json.dumps((await self._find_jobs_tool()).outputSchema)
        for rationale in ("an earlier shape", "is a bug the whole", "Revisit if"):
            assert rationale not in schema

    @staticmethod
    async def _find_jobs_tool() -> Any:
        tools = await mcp_server.build_server().list_tools()
        return next(tool for tool in tools if tool.name == "find_jobs")


class TestTheAgentIsToldHowToUseTheEngine:
    """Operating knowledge that spans tools has nowhere else to live.

    A per-tool description cannot say "call this one first", and the README is written for a human
    who is not in the loop when the agent decides what to do. Without this an agent reads
    `eligibility.status` as a label rather than the fact-versus-lead distinction it is, and ranks
    on fit alone.
    """

    def test_the_handshake_carries_instructions(self) -> None:
        options = mcp_server.build_server()._mcp_server.create_initialization_options()
        assert options.instructions

    @pytest.mark.parametrize(
        "guidance",
        [
            "describe_profile",  # the trust step, before reporting anything
            "remote-verify",  # a lead, not a fact
            "home-based",  # what a stated verdict looks like
            "all_sources_ran",  # say when the answer is partial
            "confidence",  # the sort to prefer for "what can I hold"
        ],
    )
    def test_it_covers_what_an_agent_gets_wrong_unaided(self, guidance: str) -> None:
        options = mcp_server.build_server()._mcp_server.create_initialization_options()
        assert guidance in (options.instructions or "")

    def test_it_stays_short_enough_to_ship_every_session(self) -> None:
        """It is sent on every connect, so it is guidance, not a manual."""
        options = mcp_server.build_server()._mcp_server.create_initialization_options()
        assert len(options.instructions or "") < 2500


class TestDescriptionsAreTrimmedForContext:
    """A broad live search cost an agent roughly 29,000 tokens, four fifths of it descriptions.

    The SDK sends the payload twice, once as text for the model and once as structured content, so
    the cost doubles. The engine has already finished reasoning over the full description by the
    time this runs: scoring, eligibility and relevance all read it upstream. What reaches the agent
    only has to be enough to recognise the role.
    """

    def test_a_long_description_is_cut_and_says_so(self) -> None:
        trimmed = mcp_server._trim("word " * 400)
        assert len(trimmed) < 700
        assert trimmed.endswith("[truncated, full posting at the job url]")

    def test_it_cuts_on_a_word_boundary(self) -> None:
        """A cut mid-word reads as a typo rather than a truncation."""
        trimmed = mcp_server._trim("alpha bravo " * 200)
        body = trimmed.replace(" [truncated, full posting at the job url]", "")
        assert body.split()[-1] in {"alpha", "bravo"}

    def test_a_short_description_is_left_exactly_alone(self) -> None:
        assert mcp_server._trim("Build RAG systems.") == "Build RAG systems."

    def test_trimming_happens_at_the_boundary_not_in_the_pipeline(self) -> None:
        """The full text is what the scorer and the classifier read, so shortening it upstream
        would change which jobs are found rather than only what is reported."""
        long_text = "python " * 400
        job = Job(
            title="AI Engineer",
            company="Acme",
            url="https://a/1",
            source="s",
            description=long_text,
        )
        result = SearchResult(
            query=SearchQuery(),
            jobs=[
                ScoredJob(
                    job=job,
                    fit=FitScore(),
                    relevance=Relevance(keep=True, reason="r"),
                    eligibility=Eligibility(status=EligibilityStatus.GLOBAL, reason="ok"),
                )
            ],
            coverage=[SourceCoverage(source="s", scanned=1, kept=1)],
        )
        trimmed = mcp_server._fit_for_context(result)
        assert len(trimmed.jobs[0].job.description) < len(long_text)
        assert result.jobs[0].job.description == long_text  # the original is untouched


class TestBuildServer:
    def test_builds_a_server_named_for_the_project(self) -> None:
        assert mcp_server.build_server().name == "job-seeker"

    def test_the_handshake_reports_our_version_not_the_sdks(self) -> None:
        """FastMCP takes no `version` argument, so its fallback advertises the *SDK's* version.
        Left alone the handshake says "1.28.1", which no job-seeker release will ever match.
        This pins the private-attribute workaround so an SDK change is loud, not silent.
        """
        options = mcp_server.build_server()._mcp_server.create_initialization_options()
        assert options.server_version == __version__

    async def test_exposes_the_tools_an_agent_needs(self) -> None:
        tools = await mcp_server.build_server().list_tools()
        assert {tool.name for tool in tools} == {
            "list_sources",
            "describe_engine",
            "describe_profile",
            "find_jobs",
        }

    async def test_every_tool_carries_a_description_for_the_agent(self) -> None:
        """The docstring is the agent's only signal about when to call a tool."""
        for tool in await mcp_server.build_server().list_tools():
            assert tool.description


class TestListSourcesTool:
    async def test_reports_the_registered_boards(self) -> None:
        registry.register("board-a", lambda: FakeSource("board-a", available=True))
        registry.register("board-b", lambda: FakeSource("board-b", available=False))

        payload = await _structured(mcp_server.build_server(), "list_sources")
        listed = {entry["name"]: entry["available"] for entry in payload["result"]}
        assert listed["board-a"] is True
        assert listed["board-b"] is False

    async def test_lists_the_built_in_boards(self) -> None:
        """build_server() is the composition root, so it wires the built-in adapters and the
        agent sees the real boards rather than an empty list."""
        payload = await _structured(mcp_server.build_server(), "list_sources")
        names = {entry["name"] for entry in payload["result"]}
        assert "himalayas" in names

    async def test_a_broken_adapter_does_not_crash_the_tool(self) -> None:
        """The CLI and MCP must not disagree on robustness. `sources` degrades past a broken
        adapter; `list_sources` used the fail-fast path and crashed, blinding the agent to every
        working board. It now uses the same isolating `describe()` the CLI does."""

        class BrokenAvailability:
            name = "brokenboard"

            def is_available(self) -> bool:
                raise OSError("credentials missing")

            def fetch(self, query: SearchQuery, /) -> SourceResult:
                return SourceResult(source="brokenboard")

        registry.register("brokenboard", BrokenAvailability)
        payload = await _structured(mcp_server.build_server(), "list_sources")
        listed = {entry["name"]: entry for entry in payload["result"]}
        assert listed["brokenboard"]["available"] is False
        assert "credentials missing" in listed["brokenboard"]["error"]
        assert "himalayas" in listed  # the working board is still reported


class TestDescribeEngineTool:
    """The health tool has to be able to report ill health, or it is decoration.

    `can_search` was hardcoded True, so the one question an agent would call this tool to answer,
    "is this thing configured?", was the one it could not detect. A missing profile is the most
    likely failure by a wide margin, and it surfaced only as a mid-search exception.
    """

    async def test_reports_that_search_works_when_a_profile_is_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(_write_profile(tmp_path)))
        payload = await _structured(mcp_server.build_server(), "describe_engine")
        assert payload["can_search"] is True
        assert payload["profile_problem"] == ""
        assert payload["version"] == __version__

    async def test_reports_that_it_cannot_search_with_no_profile_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("JOB_SEEKER_PROFILE", raising=False)
        payload = await _structured(mcp_server.build_server(), "describe_engine")
        assert payload["can_search"] is False
        assert "JOB_SEEKER_PROFILE" in payload["profile_problem"]

    async def test_a_malformed_profile_is_reported_here_not_discovered_mid_search(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = tmp_path / "p.md"
        broken.write_text("---\nname: : :\n---\n")
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(broken))
        payload = await _structured(mcp_server.build_server(), "describe_engine")
        assert payload["can_search"] is False
        assert payload["profile_problem"]


class TestDescribeProfileTool:
    """Every verdict is a function of the profile, so an agent must be able to say whose it is.

    A misconfigured profile does not error. It answers confidently for the wrong person, and
    without this tool neither the agent nor the seeker can see that before acting on the results.
    """

    async def test_reports_who_the_engine_is_searching_as(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        profile = tmp_path / "p.md"
        profile.write_text(
            "---\nname: Jane Doe\nheadline: Backend Engineer\n"
            "location:\n  country: Portugal\n  timezone_utc_offset: 0\n"
            "eligibility:\n  eligible_regions: [portugal, europe]\n"
            "search_terms: [Backend Engineer]\nskills:\n  '\\bpython\\b': 3\n---\n"
        )
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(profile))
        payload = await _structured(mcp_server.build_server(), "describe_profile")
        assert payload["name"] == "Jane Doe"
        assert payload["country"] == "Portugal"
        assert payload["default_search_terms"] == ["Backend Engineer"]

    async def test_it_exposes_the_eligibility_rules_that_decide_every_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rules a seeker most needs to check, because getting them wrong surfaces jobs they
        cannot legally hold, which is the one failure this product exists to prevent."""
        profile = tmp_path / "p.md"
        profile.write_text(
            "---\nlocation:\n  country: Mexico\n"
            "eligibility:\n  eligible_regions: [mexico, latam]\n"
            "  disqualifying_authorization_terms: [us citizen]\n"
            "  max_timezone_distance_hours: 3\n---\n"
        )
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(profile))
        payload = await _structured(mcp_server.build_server(), "describe_profile")
        assert payload["eligible_regions"] == ["mexico", "latam"]
        assert payload["disqualifying_authorization_terms"] == ["us citizen"]
        assert payload["max_timezone_distance_hours"] == 3


class TestFindJobsTool:
    async def test_returns_ranked_eligible_jobs_from_the_configured_profile(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """find_jobs reads the profile from JOB_SEEKER_PROFILE and searches the registered boards.

        A fake board is wired in so the tool runs offline; the point is that it composes profile +
        sources + pipeline into a structured result, not that it hits a real board.
        """
        profile_file = tmp_path / "p.md"
        profile_file.write_text(
            "---\nlocation:\n  country: Testland\nsearch_terms: [Engineer]\n"
            "skills:\n  '\\bpython\\b': 3\n---\n"
        )
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(profile_file))

        job = Job(
            title="Python Engineer",
            company="Acme",
            url="https://a/1",
            source="fake",
            hints=EligibilityHints(location_restrictions=()),
        )
        monkeypatch.setattr(defaults, "_BUILTINS", {"fake": lambda: FakeSource("fake", jobs=[job])})

        payload = await _structured(mcp_server.build_server(), "find_jobs")
        assert payload["jobs"][0]["job"]["title"] == "Python Engineer"
        assert payload["jobs"][0]["eligibility"]["status"] == "global"


class TestMainWithoutTheOptionalExtra:
    """`main()` cannot be tested past `run()`, which blocks on stdio forever. The branch before it
    can, and it is the one a user without the `mcp` extra actually hits."""

    def test_it_explains_how_to_install_the_extra_instead_of_tracebacking(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch the real `importlib.util`, not `mcp_server.importlib`: the module imports it
        # rather than re-exporting it, so reaching it through the module is a mypy-strict error.
        monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
        assert mcp_server.main() == 2
        err = capsys.readouterr().err
        assert "mcp" in err
        assert "pip install" in err


class TestTheSdkImportStaysLazy:
    def test_importing_the_module_does_not_import_the_mcp_sdk(self) -> None:
        """Asserts the runtime property, not the shape of the source.

        An AST scan for a top-level `import mcp` misses every realistic way to break this: a
        top-level `try: import mcp except ImportError`, an `importlib.import_module("mcp")`, or
        a transitive import through a helper module. All three ship a traceback to a user
        without the extra. Loading the module in a fresh interpreter and looking at sys.modules
        catches all of them, because it tests the guarantee rather than a proxy for it.
        """
        probe = (
            "import sys;"
            "import job_seeker.infrastructure.entrypoints.mcp_server;"
            "leaked = [m for m in sys.modules if m == 'mcp' or m.startswith('mcp.')];"
            "sys.exit(1 if leaked else 0)"
        )
        completed = subprocess.run([sys.executable, "-c", probe], capture_output=True)
        assert completed.returncode == 0, (
            "importing mcp_server pulled in the mcp SDK. It is an optional extra and this module "
            "is the job-seeker-mcp console script target, so that turns a missing extra into a "
            "traceback at startup instead of an install hint."
        )

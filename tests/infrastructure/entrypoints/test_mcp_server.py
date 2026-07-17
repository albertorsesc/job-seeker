"""Covers `job_seeker.infrastructure.entrypoints.mcp_server`.

`main()` is deliberately untested: it calls `server.run()`, which blocks on stdio forever. That
is exactly why `build_server()` exists separately. The end-to-end path is covered by
`storage/scripts/probe_mcp_stdio.py`, which drives the installed binary with a real client.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.fastmcp import FastMCP

from job_seeker import __version__
from job_seeker.domain.models import EligibilityHints, Job, SearchQuery, SourceResult
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
        assert {tool.name for tool in tools} == {"list_sources", "describe_engine", "find_jobs"}

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
    async def test_reports_that_search_works_and_the_version(self) -> None:
        payload = await _structured(mcp_server.build_server(), "describe_engine")
        assert payload["can_search"] is True
        assert payload["version"] == __version__


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

"""The one number both entrypoints have to state.

The CLI spells it `--scan-depth` and the MCP tool spells it `scan_depth`, and each used to carry
its own copy of the default. Two copies that must agree drift, and the drift is invisible: a
seeker and an agent asking for the same search would quietly read different amounts.

Lives here rather than beside the domain model because checking it means importing both
entrypoints, which a domain test may not do.
"""

from __future__ import annotations

from typing import Any

from job_seeker.domain.models import DEFAULT_SCAN_DEPTH
from job_seeker.infrastructure.entrypoints import cli, mcp_server


async def _published_default(parameter: str) -> Any:
    """The default an agent reads off the published input schema for `find_jobs`."""
    tools = await mcp_server.build_server().list_tools()
    tool = next(t for t in tools if t.name == "find_jobs")
    return tool.input_schema["properties"][parameter]["default"]


class TestBothEntrypointsReadTheDeclaredDepth:
    def test_the_cli_flag_defaults_to_it(self) -> None:
        assert cli._build_parser().parse_args(["find"]).scan_depth == DEFAULT_SCAN_DEPTH

    async def test_the_published_schema_shows_it_to_an_agent(self) -> None:
        """The schema is what an agent reads, so it is the copy that has to agree."""
        assert await _published_default("scan_depth") == DEFAULT_SCAN_DEPTH


class TestBothEntrypointsExposeTheFitFloor:
    """`min_fit` is applied once, in the pipeline. What the entrypoints must agree on is the name
    and the default, or a seeker and an agent asking for the same search get different lists."""

    def test_the_cli_flag_defaults_to_no_filtering(self) -> None:
        assert cli._build_parser().parse_args(["find"]).min_fit == 0.0

    async def test_the_published_schema_shows_it_to_an_agent(self) -> None:
        assert await _published_default("min_fit") == 0.0

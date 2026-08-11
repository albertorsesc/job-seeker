"""The one number both entrypoints have to state.

The CLI spells it `--scan-depth` and the MCP tool spells it `scan_depth`, and each used to carry
its own copy of the default. Two copies that must agree drift, and the drift is invisible: a
seeker and an agent asking for the same search would quietly read different amounts.

Lives here rather than beside the domain model because checking it means importing both
entrypoints, which a domain test may not do.
"""

from __future__ import annotations

import inspect

from job_seeker.domain.models import DEFAULT_SCAN_DEPTH
from job_seeker.infrastructure.entrypoints import cli, mcp_server


class TestBothEntrypointsReadTheDeclaredDepth:
    def test_the_cli_flag_defaults_to_it(self) -> None:
        assert cli._build_parser().parse_args(["find"]).scan_depth == DEFAULT_SCAN_DEPTH

    def test_the_mcp_tool_parameter_defaults_to_it(self) -> None:
        tool = mcp_server.build_server()._tool_manager.get_tool("find_jobs")
        assert tool is not None
        assert inspect.signature(tool.fn).parameters["scan_depth"].default == DEFAULT_SCAN_DEPTH

    def test_the_published_schema_shows_it_to_an_agent(self) -> None:
        """An agent reads the default off the tool schema, not off the signature."""
        tool = mcp_server.build_server()._tool_manager.get_tool("find_jobs")
        assert tool is not None
        assert tool.parameters["properties"]["scan_depth"]["default"] == DEFAULT_SCAN_DEPTH

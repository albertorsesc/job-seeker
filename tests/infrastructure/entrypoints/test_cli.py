"""Covers `job_seeker.infrastructure.entrypoints.cli`."""

from __future__ import annotations

import pytest

from job_seeker import __version__
from job_seeker.infrastructure.entrypoints import cli
from job_seeker.infrastructure.sources import registry

from ..conftest import FakeSource


class TestSourcesCommand:
    def test_lists_the_built_in_boards(self, capsys: pytest.CaptureFixture[str]) -> None:
        """main() is the composition root: it wires the built-in adapters, so `sources` lists
        the real boards a fresh install ships with."""
        assert cli.main(["sources"]) == 0
        assert "himalayas" in capsys.readouterr().out

    def test_says_so_plainly_when_the_registry_is_empty(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The defensive branch, reached by calling the helper directly: main() always registers
        the built-ins, but the message still needs to be right if a build ever ships none."""
        assert cli._sources() == 0
        assert "No job boards are registered yet" in capsys.readouterr().out

    def test_lists_each_board_and_whether_it_can_run(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        registry.register("board-a", lambda: FakeSource("board-a", available=True))
        registry.register("board-b", lambda: FakeSource("board-b", available=False))

        assert cli.main(["sources"]) == 0
        out = capsys.readouterr().out
        assert "board-a" in out
        assert "available" in out
        assert "board-b" in out
        assert "unavailable" in out

    def test_an_unavailable_board_is_still_listed(self, capsys: pytest.CaptureFixture[str]) -> None:
        """ "exists but cannot run" and "does not exist" are different answers, and the seeker
        needs to be able to tell which one they are looking at."""
        registry.register("jobspy", lambda: FakeSource("jobspy", available=False))
        assert cli.main(["sources"]) == 0
        out = capsys.readouterr().out
        assert "jobspy" in out
        assert "unavailable" in out
        assert "No job boards are registered" not in out

    def test_a_broken_adapter_does_not_hide_the_working_ones(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The regression: one adapter whose constructor raised took the whole command down with
        an unhandled traceback, on the command you run precisely because something is broken."""

        def broken() -> FakeSource:
            raise RuntimeError("credentials file not found")

        registry.register("himalayas", lambda: FakeSource("himalayas"))
        registry.register("jobspy", broken)

        assert cli.main(["sources"]) == 0
        out = capsys.readouterr().out
        assert "himalayas" in out
        assert "available" in out
        assert "broken" in out
        assert "credentials file not found" in out


class TestFindCommand:
    def test_refuses_rather_than_returning_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The failure that matters: an empty result would be indistinguishable from a search
        that legitimately found nothing, which is the worst outcome for a tool whose whole job
        is telling you what is out there."""
        assert cli.main(["find"]) == 2
        assert "not implemented yet" in capsys.readouterr().err

    def test_says_nothing_on_stdout_so_a_pipe_gets_no_fake_result(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.main(["find"])
        assert capsys.readouterr().out == ""


class TestTopLevel:
    def test_version_reports_the_package_version(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["--version"])
        assert exit_info.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_no_command_prints_help_and_exits_non_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.main([]) == 2
        assert "usage: job-seeker" in capsys.readouterr().err

    def test_no_command_keeps_stdout_clean(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Same discipline as `find`: a failure path must not put text on stdout, or
        `job-seeker | jq` receives a page of help and a non-zero code."""
        cli.main([])
        assert capsys.readouterr().out == ""

    def test_an_unknown_command_is_rejected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Asserting the code, not merely that it exited: SystemExit(0) would mean argparse
        accepted the garbage, and the test name would still read as passing."""
        with pytest.raises(SystemExit) as exit_info:
            cli.main(["definitely-not-a-command"])
        assert exit_info.value.code == 2

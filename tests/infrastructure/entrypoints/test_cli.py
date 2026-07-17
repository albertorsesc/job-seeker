"""Covers `job_seeker.infrastructure.entrypoints.cli`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_seeker import __version__
from job_seeker.domain.models import EligibilityHints, Job
from job_seeker.infrastructure.entrypoints import cli
from job_seeker.infrastructure.sources import defaults, registry

from ..conftest import FakeSource


def _write_profile(tmp_path: Path) -> Path:
    path = tmp_path / "profile.md"
    path.write_text(
        "---\nlocation:\n  country: Testland\nsearch_terms: [Engineer]\n"
        "skills:\n  '\\bpython\\b': 3\n---\n"
    )
    return path


def _wire_fake_board(monkeypatch: pytest.MonkeyPatch, *jobs: Job) -> None:
    monkeypatch.setattr(
        defaults, "_BUILTINS", {"fake": lambda: FakeSource("fake", jobs=list(jobs))}
    )


def _a_job(title: str = "Python Engineer") -> Job:
    return Job(
        title=title,
        company="Acme",
        url=f"https://a/{title}".replace(" ", "-"),
        source="fake",
        hints=EligibilityHints(location_restrictions=()),
    )


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
    def test_without_a_configured_profile_it_refuses_clearly(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("JOB_SEEKER_PROFILE", raising=False)
        assert cli.main(["find"]) == 2
        err = capsys.readouterr().err
        assert "JOB_SEEKER_PROFILE" in err
        assert capsys.readouterr().out == ""  # nothing on stdout on the failure path

    def test_runs_the_search_and_prints_a_report(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_fake_board(monkeypatch, _a_job())
        code = cli.main(["find", "--profile", str(_write_profile(tmp_path)), "--format", "json"])
        assert code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["jobs"][0]["job"]["title"] == "Python Engineer"

    def test_writes_to_a_file_when_out_is_given(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_fake_board(monkeypatch, _a_job())
        out = tmp_path / "report.html"
        code = cli.main(
            [
                "find",
                "--profile",
                str(_write_profile(tmp_path)),
                "--format",
                "html",
                "--out",
                str(out),
            ]
        )
        assert code == 0
        assert out.read_text().lstrip().startswith("<!doctype html>")
        assert capsys.readouterr().out == ""  # the report went to the file, not stdout

    def test_a_bad_profile_path_is_a_clear_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli.main(["find", "--profile", "/no/such/profile.md"]) == 2
        assert "not found" in capsys.readouterr().err

    def test_an_unwritable_out_path_is_a_clear_error_not_a_traceback(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_fake_board(monkeypatch, _a_job())
        bad_out = tmp_path / "no-such-dir" / "report.html"
        code = cli.main(["find", "--profile", str(_write_profile(tmp_path)), "--out", str(bad_out)])
        assert code == 2
        assert "Could not write" in capsys.readouterr().err

    def test_role_include_alone_is_enough_to_search_without_terms(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The CLI must not refuse when role_include can narrow, or it disagrees with the MCP
        tool, which runs the same profile fine."""
        _wire_fake_board(monkeypatch, _a_job(title="Backend Engineer"))
        profile = tmp_path / "p.md"
        profile.write_text("---\nlocation:\n  country: Testland\nrole_include: [engineer]\n---\n")
        code = cli.main(["find", "--profile", str(profile), "--format", "json"])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["jobs"][0]["job"]["title"] == "Backend Engineer"

    def test_no_terms_and_no_role_include_asks_to_narrow(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_fake_board(monkeypatch, _a_job())
        profile = tmp_path / "p.md"
        profile.write_text("---\nlocation:\n  country: Testland\n---\n")
        assert cli.main(["find", "--profile", str(profile)]) == 2
        assert "narrow" in capsys.readouterr().err


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

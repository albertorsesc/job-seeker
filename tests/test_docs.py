"""Keep the documentation honest about things the code already knows.

Prose is the one part of this project with no failing state. Everything else has a test, a type or
a linter; a sentence that stops being true just sits there, and this repo has watched that happen
more than once. These check only the claims that are mechanically checkable: which boards exist,
which CLI commands exist, and which files the docs point at. Everything else in the docs is
reasoning, which no test can hold to account.

Deliberately not tested: the wording of any explanation, the board-behaviour notes (they record
live runs on a date, and a board changing does not make the record of that run false), and the
profile schema, which is not documented in prose at all. `examples/profile.example.md` is the
schema, it is loaded and validated by `tests/domain/test_profile.py`, and a second copy written out
in words would be the unverified one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from job_seeker.infrastructure.entrypoints import cli
from job_seeker.infrastructure.sources import defaults

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def _invocations(markdown: str) -> set[str]:
    """The subcommands actually invoked in a document, read from code and never from prose.

    "job-seeker aggregates postings from several boards" opens the README and is a sentence about
    the tool, not a use of it. A check that cannot tell those apart reports a failure nobody can
    act on, which is worse than not checking.
    """
    fenced: list[str] = []
    inside = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            inside = not inside
            continue
        if inside:
            fenced.append(line)
    from_blocks = re.findall(r"^\s*job-seeker ([a-z-]+)", "\n".join(fenced), re.MULTILINE)
    from_spans = re.findall(r"`job-seeker ([a-z-]+)", markdown)
    return set(from_blocks) | set(from_spans)


def _read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


class TestTheBoardsAreDocumented:
    @pytest.mark.parametrize("board", sorted(defaults._BUILTINS))
    def test_every_registered_board_appears_in_the_sources_doc(self, board: str) -> None:
        """A fifth board that nobody documented is one a contributor cannot learn from, and the
        table of what each board returns is the reason that file exists."""
        assert board in _read("sources.md").lower().replace(" ", "")

    def test_the_doc_names_no_board_the_registry_does_not_have(self) -> None:
        """The other direction, which is how a removed board leaves a ghost behind. Checked against
        the table's own rows rather than the prose, since the prose legitimately discusses boards
        that are planned or rejected."""
        rows = [
            line
            for line in _read("sources.md").splitlines()
            if line.startswith("| ") and "|" in line
        ]
        named = {row.split("|")[1].strip().lower().replace(" ", "") for row in rows[2:]}
        registered = {name.lower() for name in defaults._BUILTINS}
        assert {board for board in named if board and board not in registered} == set()


class TestTheCommandsAreDocumented:
    @pytest.mark.parametrize("command", ["find", "sources", "mark", "unmark"])
    def test_every_cli_command_appears_in_the_readme(self, command: str) -> None:
        """The README is the front door. A command nobody can discover may as well not ship."""
        assert f"job-seeker {command}" in (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_readme_documents_no_command_that_does_not_exist(self) -> None:
        """Read from code only, never from prose. "job-seeker aggregates postings" is a sentence
        about the tool, not an invocation of it, and a test that cannot tell the difference reports
        a failure nobody can act on."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        mentioned = {word for word in _invocations(readme) if not word.startswith("-")}
        parser = cli._build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.dest == "command"]
        real = set(actions[0].choices or {}) if actions else set()
        assert mentioned - real - {"mcp"} == set()  # job-seeker-mcp is the other console script


class TestTheLinksResolve:
    @pytest.mark.parametrize("source", ["README.md", "CLAUDE.md"])
    def test_every_docs_link_points_at_a_file_that_exists(self, source: str) -> None:
        """The cheapest documentation failure there is, and the one a reader hits first."""
        text = (ROOT / source).read_text(encoding="utf-8")
        for target in re.findall(r"\]\((docs/[^)#]+)\)", text):
            assert (ROOT / target).exists(), f"{source} links to a missing {target}"

    def test_the_docs_directory_is_not_empty(self) -> None:
        assert {path.name for path in DOCS.glob("*.md")} >= {"architecture.md", "sources.md"}

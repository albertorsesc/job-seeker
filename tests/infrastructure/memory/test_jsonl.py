"""Covers `job_seeker.infrastructure.memory.jsonl`.

The only place that touches a real file, so this is the only test module that needs one. Everything
here runs against `tmp_path`: the suite must never write into the journal of whoever is at this
machine, which is what the autouse fixture in `tests/conftest.py` exists to guarantee.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_seeker.domain.memory import PostingDecision, Sighting, posting_handle
from job_seeker.infrastructure.memory import JsonlPostingMemory

KEY = "acme|senior ai engineer"


def _sighting(key: str = KEY, url: str = "https://a.test/1") -> Sighting:
    return Sighting(key=key, title="Senior AI Engineer", company="Acme", source="fake", url=url)


def _memory(tmp_path: Path) -> JsonlPostingMemory:
    return JsonlPostingMemory(tmp_path / "state" / "postings.jsonl")


class TestReading:
    def test_a_missing_journal_is_empty_and_available(self, tmp_path: Path) -> None:
        """An ordinary first run, and the run after the seeker deletes the file. Empty is not the
        same as unreadable: everything is genuinely new here."""
        recalled = _memory(tmp_path).recall()
        assert recalled.available is True
        assert recalled.records == {}
        assert recalled.error == ""

    def test_a_journal_that_cannot_be_read_says_so_rather_than_looking_empty(
        self, tmp_path: Path
    ) -> None:
        """A directory where the file should be. Reported, not raised, and never as an empty
        recollection: empty would silently stop honouring every dismissal."""
        path = tmp_path / "postings.jsonl"
        path.mkdir()
        recalled = JsonlPostingMemory(path).recall()
        assert recalled.available is False
        assert recalled.error

    def test_a_damaged_line_costs_one_posting_not_the_journal(self, tmp_path: Path) -> None:
        """The reason for JSONL over a single JSON document."""
        memory = _memory(tmp_path)
        memory.record((_sighting(), _sighting(key="other|role", url="https://a.test/2")))
        lines = memory.path.read_text().splitlines()
        lines.insert(2, "{not json at all")
        memory.path.write_text("\n".join(lines) + "\n")
        assert set(memory.recall().records) == {KEY, "other|role"}


class TestWriting:
    def test_a_journal_round_trips(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        record = memory.recall().records[KEY]
        assert (record.title, record.company, record.times_seen) == (
            "Senior AI Engineer",
            "Acme",
            1,
        )

    def test_seeing_a_posting_again_counts_it_without_moving_first_seen(
        self, tmp_path: Path
    ) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        first = memory.recall().records[KEY].first_seen_at
        memory.record((_sighting(),))
        again = memory.recall().records[KEY]
        assert again.times_seen == 2
        assert again.first_seen_at == first

    def test_the_first_write_creates_the_directory_and_the_file(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        assert not memory.path.exists()
        memory.record((_sighting(),))
        assert memory.path.exists()

    def test_it_records_which_urls_a_posting_was_seen_at(self, tmp_path: Path) -> None:
        """One role on two boards has two apply URLs, and either is something the seeker might
        paste back at the terminal."""
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        memory.record((_sighting(url="https://b.test/9"),))
        assert set(memory.recall().records[KEY].urls) == {"https://a.test/1", "https://b.test/9"}


class TestDeciding:
    def test_a_decision_is_recorded_and_survives_a_reread(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        memory.decide((KEY,), PostingDecision.APPLIED, "referred by K")
        record = memory.recall().records[KEY]
        assert record.decision is PostingDecision.APPLIED
        assert record.note == "referred by K"

    def test_a_decision_can_be_cleared(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        memory.decide((KEY,), PostingDecision.DISMISSED, "")
        memory.decide((KEY,), None, "")
        record = memory.recall().records[KEY]
        assert record.decision is None
        assert record.decided_at is None

    def test_a_reference_resolves_as_a_handle_a_key_or_a_url(self, tmp_path: Path) -> None:
        """Whichever of the three the seeker already has in front of them."""
        for ref in (KEY, posting_handle(KEY), "https://a.test/1"):
            memory = _memory(tmp_path / ref[:6].replace("/", "_"))
            memory.record((_sighting(),))
            written = memory.decide((ref,), PostingDecision.DISMISSED, "")
            assert written.written == 1, ref

    def test_one_unresolved_reference_writes_nothing_at_all(self, tmp_path: Path) -> None:
        """All or nothing. A half-applied batch leaves the seeker unable to tell which half landed,
        and a dismissal on the wrong posting is worse than a typo being reported."""
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        written = memory.decide((KEY, "jk_deadbeefdeadbeef"), PostingDecision.DISMISSED, "")
        assert written.unknown == ("jk_deadbeefdeadbeef",)
        assert written.written == 0
        assert memory.recall().records[KEY].decision is None

    def test_a_url_two_postings_share_never_resolves(self, tmp_path: Path) -> None:
        """Himalayas takes its url from the board's applicationLink, which for some employers is a
        generic careers page. Resolving through one would attach a dismissal to a posting the
        seeker never meant, and hide it forever without their ever seeing it once."""
        memory = _memory(tmp_path)
        shared = "https://acme.test/careers"
        memory.record((_sighting(url=shared), _sighting(key="acme|data engineer", url=shared)))
        written = memory.decide((shared,), PostingDecision.DISMISSED, "")
        assert written.unknown == (shared,)


class TestTheSearchNeverClobbersAMark:
    def test_a_decision_made_while_a_search_was_running_survives_it(self, tmp_path: Path) -> None:
        """The concurrency case, tested without threads: a search reads the journal, the seeker
        marks something, and the search then writes. A run takes seconds and a mark takes one, so
        this is a Tuesday, not a corner case."""
        memory = _memory(tmp_path)
        memory.record((_sighting(),))

        recalled = memory.recall()  # the search reads
        memory.decide((KEY,), PostingDecision.DISMISSED, "")  # the seeker marks, mid-flight
        memory.record((_sighting(),))  # the search writes what it delivered

        assert recalled.records[KEY].decision is None  # the search never saw the mark
        assert memory.recall().records[KEY].decision is PostingDecision.DISMISSED


class TestForwardCompatibility:
    def test_a_field_this_build_does_not_know_survives_a_rewrite(self, tmp_path: Path) -> None:
        """The journal is written by other builds of this same software, so dropping a field you do
        not recognise loses a newer build's data the first time an older one runs."""
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        lines = memory.path.read_text().splitlines()
        record = json.loads(lines[1])
        record["invented_by_a_later_version"] = "keep me"
        lines[1] = json.dumps(record)
        memory.path.write_text("\n".join(lines) + "\n")

        memory.record((_sighting(),))
        rewritten = json.loads(memory.path.read_text().splitlines()[1])
        assert rewritten.get("invented_by_a_later_version") == "keep me"


class TestThePath:
    def test_the_environment_variable_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chosen = tmp_path / "elsewhere.jsonl"
        monkeypatch.setenv("JOB_SEEKER_STATE", str(chosen))
        assert JsonlPostingMemory.from_env().path == chosen

    def test_it_falls_back_to_the_state_directory_not_the_repo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Never inside the project: this records which companies someone applied to and when."""
        monkeypatch.delenv("JOB_SEEKER_STATE", raising=False)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
        assert JsonlPostingMemory.from_env().path == tmp_path / "job-seeker" / "postings.jsonl"


class TestTimestamps:
    def test_the_run_time_is_remembered_for_the_next_run(self, tmp_path: Path) -> None:
        memory = _memory(tmp_path)
        before = datetime.now(UTC)
        memory.record((_sighting(),))
        previous = memory.recall().previous_run_at
        assert previous is not None and previous >= before


class TestAFailedWriteLeavesTheOldJournalIntact:
    """The observable half of writing through a temp file and `os.replace`.

    The fsync that goes with it guards against power loss, which no unit test can stage. This
    covers what can be staged: a write that dies partway must not leave the seeker with a truncated
    record of what they applied to.
    """

    def test_the_previous_contents_survive_a_write_that_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))
        before = memory.path.read_text()

        def explode(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("job_seeker.infrastructure.memory.jsonl.os.replace", explode)
        written = memory.record((_sighting(key="other|role"),))

        assert written.error
        assert memory.path.read_text() == before

    def test_it_leaves_no_temporary_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        memory = _memory(tmp_path)
        memory.record((_sighting(),))

        def explode(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("job_seeker.infrastructure.memory.jsonl.os.replace", explode)
        memory.record((_sighting(key="other|role"),))

        assert list(memory.path.parent.glob("*.tmp")) == []

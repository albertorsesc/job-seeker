"""Covers `job_seeker.domain.services.history` and the `PostingHistory` it produces.

The stage that decides what the seeker has already been shown. Its one job is to answer three ways,
not two: seen before, never seen, and could not say. The third is the one that matters, because an
unreadable journal that answers "never seen" badges the whole list as new and quietly stops
honouring every dismissal the seeker made.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_seeker.domain.memory import PostingDecision, PostingRecord, Recollection, posting_handle
from job_seeker.domain.models import Job, PostingHistory
from job_seeker.domain.services.deduplicator import posting_identity
from job_seeker.domain.services.history import HistoryClassifier

WHEN = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def _job(title: str = "Senior AI Engineer", company: str = "Acme") -> Job:
    return Job(title=title, company=company, url="https://a.test/1", source="fake")


def _remembered(job: Job, **fields: object) -> Recollection:
    key = posting_identity(job)
    record = PostingRecord(
        key=key,
        title=job.title,
        company=job.company,
        first_seen_at=WHEN,
        last_seen_at=WHEN + timedelta(days=7),
        times_seen=3,
        **fields,  # type: ignore[arg-type]
    )
    return Recollection(records={key: record}, available=True)


class TestWhatMemoryKnew:
    def test_a_posting_never_delivered_before_is_new(self) -> None:
        history = HistoryClassifier().classify(Recollection(available=True), _job())
        assert history is not None
        assert history.is_new is True
        assert history.times_seen == 0

    def test_a_posting_already_delivered_is_not_new(self) -> None:
        job = _job()
        history = HistoryClassifier().classify(_remembered(job), job)
        assert history is not None
        assert history.is_new is False

    def test_it_reports_the_state_from_before_this_run(self) -> None:
        """`times_seen` counts deliveries before now, so a first sighting reads 0 rather than 1.
        That is what lets a seeker sanity-check a NEW badge instead of trusting it."""
        job = _job()
        history = HistoryClassifier().classify(_remembered(job), job)
        assert history is not None
        assert (history.times_seen, history.first_seen_at) == (3, WHEN)

    def test_it_carries_the_seekers_decision(self) -> None:
        job = _job()
        history = HistoryClassifier().classify(
            _remembered(job, decision=PostingDecision.DISMISSED, decided_at=WHEN), job
        )
        assert history is not None
        assert history.decision is PostingDecision.DISMISSED

    def test_the_same_posting_from_another_board_is_still_known(self) -> None:
        """The key is company and title, so the board a posting arrived from does not change who
        it is. Two boards syndicating one role must not make it new twice."""
        remembered = _remembered(_job())
        elsewhere = Job(
            title="Senior AI Engineer", company="Acme", url="https://other.test/9", source="other"
        )
        history = HistoryClassifier().classify(remembered, elsewhere)
        assert history is not None and history.is_new is False


class TestWhenMemoryCannotAnswer:
    def test_an_unreadable_store_yields_no_history_at_all(self) -> None:
        """Not a history claiming the posting is new. Absent and empty must not collapse: the
        caller has to be able to tell "never shown you this" from "cannot say"."""
        assert HistoryClassifier().classify(Recollection(available=False), _job()) is None

    def test_memory_turned_off_yields_no_history_either(self) -> None:
        classifier = HistoryClassifier()
        assert classifier.classify(Recollection(available=False, enabled=False), _job()) is None


class TestIsNewIsDerived:
    def test_a_caller_cannot_declare_a_seen_posting_new(self) -> None:
        history = PostingHistory(key="k", handle="jk_x", first_seen_at=WHEN, is_new=True)
        assert history.is_new is False

    def test_it_survives_a_json_round_trip(self) -> None:
        history = PostingHistory(key="k", handle="jk_x", first_seen_at=WHEN)
        assert PostingHistory.model_validate_json(history.model_dump_json()).is_new is False

    def test_it_is_in_the_published_contract_and_marked_read_only(self) -> None:
        schema = PostingHistory.model_json_schema()["properties"]["is_new"]
        assert schema.get("readOnly") is True


class TestTheHandle:
    def test_it_is_stable_for_one_identity(self) -> None:
        assert posting_handle("acme|senior ai engineer") == posting_handle(
            "acme|senior ai engineer"
        )

    def test_two_identities_do_not_share_a_handle(self) -> None:
        assert posting_handle("acme|a") != posting_handle("acme|b")

    @pytest.mark.parametrize("identity", ["acme|senior ai engineer", "a, inc.|x/y (remote)"])
    def test_it_is_safe_to_paste_at_a_shell(self, identity: str) -> None:
        """An identity carries spaces, a pipe and whatever punctuation a company put in its name,
        which makes it a poor command-line argument. The handle is what the seeker copies."""
        handle = posting_handle(identity)
        assert handle.startswith("jk_")
        assert handle[3:].isalnum() and handle[3:].islower()

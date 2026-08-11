"""Decide what this run has already shown the seeker.

Two questions look like one and are not. "Is this key in the store" is a fact about a file, and it
arrives here already answered, as a `PostingRecord` or nothing. "Does that count as new for this
seeker" is reasoning, and reasoning belongs in the centre of the hexagon beside scoring and
eligibility rather than inside the adapter that happened to read the file.

Newness needs no clock and no configuration. A posting is new when memory has no record of the
engine ever putting it in front of the seeker. A calendar horizon ("new means seen in the last
seven days") was considered and rejected: it makes the badge's meaning depend on when the seeker
happened to open their laptop, so a five-day gap re-badges last week's whole list and a nine-day
gap does not. A badge that cries wolf is a badge nobody reads, and reading it is the entire feature.
"""

from __future__ import annotations

from job_seeker.domain.memory import Recollection, posting_handle
from job_seeker.domain.models import Job, PostingHistory
from job_seeker.domain.services.deduplicator import posting_identity


class HistoryClassifier:
    """This run's verdict about each posting, given what memory remembered.

    Stateless and pure. It takes the recollection as an argument rather than holding it, so one
    classifier serves every run and nothing here has to be rebuilt when the file changes.
    """

    def classify(self, recollection: Recollection, job: Job, /) -> PostingHistory | None:
        """What memory knew about this posting, or None when memory could not answer.

        **None is not "never seen".** An unreadable store that answered "new" to everything would
        badge the whole list as new and, worse, would stop hiding the postings the seeker banned
        while looking exactly like an ordinary run. So absent and empty stay apart here as they do
        everywhere else, and the caller decides what to say about it.
        """
        if not recollection.available:
            return None
        key = posting_identity(job)
        record = recollection.records.get(key)
        if record is None:
            return PostingHistory(key=key, handle=posting_handle(key))
        return PostingHistory(
            key=key,
            handle=posting_handle(key),
            times_seen=record.times_seen,
            first_seen_at=record.first_seen_at,
            decision=record.decision,
            decided_at=record.decided_at,
        )

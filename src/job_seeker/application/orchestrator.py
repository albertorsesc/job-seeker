"""The use case: turn a query into the jobs the seeker can actually hold, ranked.

This is the combination spine. It orchestrates; it holds no business rules of its own. Fetching is
a port (`JobSource`), and the reasoning, dedup, scoring, eligibility, is domain services. The
orchestrator only sequences them and merges their results into a `SearchResult`.

It depends on abstractions: sources arrive as a `list[JobSource]` by constructor injection, never
read from a registry here, so the core stays free of infrastructure. The composition root builds
the list and hands it in.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from job_seeker.application.ports import JobSource, PostingMemory
from job_seeker.domain.memory import (
    MemoryWrite,
    PostingDecision,
    Recollection,
    Sighting,
)
from job_seeker.domain.models import (
    STATED_STATUSES,
    EligibilityStatus,
    Job,
    MemoryStatus,
    Relevance,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SortOrder,
    SourceCoverage,
    SourceResult,
)
from job_seeker.domain.profile import Profile
from job_seeker.domain.services import (
    Deduplicator,
    EligibilityClassifier,
    ProfileScorer,
    RelevanceFilter,
)
from job_seeker.domain.services.history import HistoryClassifier

# Bound the fan-out: enough to run every board concurrently without spawning an unbounded pool if
# a deployment ever registers dozens. Sources are I/O-bound, so threads (not processes) fit.
_MAX_WORKERS = 8


def _within_age(posted_at: datetime | None, max_age_days: int | None) -> bool:
    """A central age backstop. `max_age_days` is part of the query contract, and a source that
    ignores it must not leak stale postings. An undated posting is kept: age cannot be judged, so
    dropping it would be a guess in the wrong direction."""
    if max_age_days is None or posted_at is None:
        return True
    return posted_at >= datetime.now(UTC) - timedelta(days=max_age_days)


class JobSeeker:
    """Runs a search across sources and returns ranked, eligibility-filtered results."""

    def __init__(
        self,
        sources: list[JobSource],
        deduplicator: Deduplicator,
        relevance: RelevanceFilter,
        scorer: ProfileScorer,
        classifier: EligibilityClassifier,
        profile: Profile,
        memory: PostingMemory,
        history: HistoryClassifier,
    ) -> None:
        self._sources = sources
        self._dedup = deduplicator
        self._relevance = relevance
        self._scorer = scorer
        self._classifier = classifier
        self._profile = profile
        self._memory = memory
        self._history = history

    @classmethod
    def default(
        cls, sources: list[JobSource], profile: Profile, memory: PostingMemory
    ) -> JobSeeker:
        """Wire the standard domain services from a profile. The composition root's entry point.

        `memory` is required rather than defaulted. A default would let a caller forget it and get
        a silently forgetful engine, which is the same shape of mistake as a salary figure with no
        currency: the value looks fine and means something else.
        """
        return cls(
            sources=sources,
            deduplicator=Deduplicator(),
            relevance=RelevanceFilter(profile),
            scorer=ProfileScorer(profile),
            classifier=EligibilityClassifier(profile),
            profile=profile,
            memory=memory,
            history=HistoryClassifier(),
        )

    def run(self, query: SearchQuery) -> SearchResult:
        """Fan out, dedupe, filter to what was searched for, score, classify, keep the holdable,
        rank. Returns jobs plus honest coverage."""
        recollection = self._guarded_recall()
        source_results = self._fetch_all(query)
        collected = [job for result in source_results for job in result.jobs]
        fresh = [job for job in collected if _within_age(job.posted_at, query.max_age_days)]
        deduped = self._dedup.dedupe(fresh)
        # Relevance before scoring: no point scoring an Aluminum Director for an AI search, and it
        # is what makes the query's terms actually narrow the result. Its verdict is carried, not
        # discarded: a kept job records why it was on-topic, the way every other stage explains
        # itself.
        assessed = self._relevance.assess_all(deduped, query.terms)
        suitable = [
            scored
            for job, relevance in assessed
            if relevance.keep
            and (scored := self._evaluate(job, relevance, query, recollection)) is not None
        ]
        # A separate pass rather than two more clauses inside `_is_suitable`, because the counts are
        # part of the answer: a list that shrank has to be able to say why, and "nothing new this
        # week" is a different report from "twelve new". Folding these into the suitability check
        # would make both counts unreachable.
        kept, dismissed_hidden, not_new_hidden = _narrow_by_memory(suitable, query)
        ranked = sorted(kept, key=_ordering(query.sort), reverse=True)
        # The cap is applied after ranking, which is the whole point of having it: asking for five
        # results now returns the five best, where the old per-source depth returned five of
        # whatever happened to be fetched first.
        capped = ranked[: query.max_results] if query.max_results is not None else ranked
        write = self._guarded_record(capped)
        return SearchResult(
            # Coverage counts what each board *matched*, before the cap, so `sum(kept)` exceeding
            # `len(jobs)` is exactly how a caller sees that the cap bit. Counting post-cap would
            # make a board that contributed twenty matches look like it contributed two.
            query=query,
            jobs=capped,
            coverage=self._coverage(source_results, ranked),
            memory=MemoryStatus(
                enabled=recollection.enabled,
                available=recollection.available,
                recorded=bool(capped) and not write.error,
                error=recollection.error,
                write_error=write.error,
                known=len(recollection.records),
                new=sum(1 for scored in ranked if scored.history and scored.history.is_new),
                dismissed_hidden=dismissed_hidden,
                not_new_hidden=not_new_hidden,
                previous_run_at=recollection.previous_run_at,
            ),
        )

    def _guarded_recall(self) -> Recollection:
        """Recall, turning any escape into an unavailable memory.

        `PostingMemory.recall` is contracted never to raise, but that is a docstring rather than an
        enforcement, exactly as it is for `JobSource.fetch`. A careless adapter must cost the seeker
        their newness markers, not their search.
        """
        try:
            return self._memory.recall()
        except Exception as exc:  # noqa: BLE001 - an adapter may do anything
            return Recollection(available=False, error=f"{type(exc).__name__}: {exc}")

    def _guarded_record(self, delivered: list[ScoredJob]) -> MemoryWrite:
        """Write down what this run delivered. Same guard, same reason."""
        sightings = tuple(
            Sighting(
                key=scored.history.key,
                title=scored.job.title,
                company=scored.job.company,
                source=scored.job.source,
                url=scored.job.url,
            )
            for scored in delivered
            if scored.history is not None
        )
        try:
            return self._memory.record(sightings)
        except Exception as exc:  # noqa: BLE001 - an adapter may do anything
            return MemoryWrite(error=f"{type(exc).__name__}: {exc}")

    def _fetch_all(self, query: SearchQuery) -> list[SourceResult]:
        if not self._sources:
            return []
        workers = min(_MAX_WORKERS, len(self._sources))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda source: self._fetch_one(source, query), self._sources))

    def _fetch_one(self, source: JobSource, query: SearchQuery) -> SourceResult:
        """Fetch from one source, turning any escape into a reported failure.

        `JobSource.fetch` is contracted never to raise, but that is a docstring, not an
        enforcement. This is the one seam the whole run's survival depends on, so it does not
        trust the contract: a careless adapter that raises becomes a failed SourceResult, not a
        dead run.
        """
        try:
            return source.fetch(query)
        except Exception as exc:  # noqa: BLE001 - deliberately catch-all: an adapter may do anything
            return SourceResult(source=source.name, error=f"{type(exc).__name__}: {exc}")

    def _evaluate(
        self, job: Job, relevance: Relevance, query: SearchQuery, recollection: Recollection
    ) -> ScoredJob | None:
        """Score and classify one job; return a ScoredJob, or None if the seeker cannot hold it.

        The relevance verdict is decided upstream (it gates whether we score at all) and passed in
        so the ScoredJob can carry why the posting was on-topic alongside its fit and eligibility.
        """
        fit = self._scorer.score(job)
        eligibility = self._classifier.classify(job)
        scored = ScoredJob(
            job=job,
            fit=fit,
            relevance=relevance,
            eligibility=eligibility,
            history=self._history.classify(recollection, job),
        )
        return scored if self._is_suitable(scored, query) else None

    def _is_suitable(self, scored: ScoredJob, query: SearchQuery) -> bool:
        eligibility = scored.eligibility
        if not eligibility.is_eligible:
            return False
        # Below the caller's fit floor. Applied here rather than in an entrypoint so the CLI and
        # the MCP tool cannot mean different things by the same number.
        if scored.fit.value < query.min_fit:
            return False
        # A per-search narrowing on top of the profile's standing preference. `stated_only` drops
        # anything the board did not affirmatively clear, which is the difference between "here is
        # a lead" and "here is a job you can hold".
        if query.stated_only and eligibility.status not in STATED_STATUSES:
            return False
        # An unverifiable posting counts only if the profile opts in (card 013): is_eligible alone
        # treats REMOTE_UNVERIFIED as holdable, so the opt-out is applied here as an active filter.
        opted_out_of_unverified = (
            eligibility.status is EligibilityStatus.REMOTE_UNVERIFIED
            and not self._profile.eligibility.include_unverified
        )
        return not opted_out_of_unverified

    @staticmethod
    def _coverage(
        source_results: list[SourceResult], ranked: list[ScoredJob]
    ) -> list[SourceCoverage]:
        """Per-source coverage. `kept` counts survivors attributed to each source after dedup, so
        a posting merged across boards is credited to the one record that won."""
        kept = Counter(scored.job.source for scored in ranked)
        return [
            SourceCoverage(
                source=result.source,
                scanned=result.scanned,
                kept=kept.get(result.source, 0),
                truncated=result.truncated,
                error=result.error,
            )
            for result in source_results
        ]


def _ordering(order: SortOrder) -> Callable[[ScoredJob], tuple[int, ...]]:
    """The sort key for a ranking order.

    Both rank on `fit.raw`, the exact integer, rather than the normalized value: the same order
    within a run, but ordering never turns on a rounded float. `CONFIDENCE` puts certainty first
    and uses fit to break ties inside each tier, so it groups rather than reshuffles.
    """
    if order is SortOrder.CONFIDENCE:
        return lambda scored: (
            int(scored.eligibility.status in STATED_STATUSES),
            scored.fit.raw,
        )
    return lambda scored: (scored.fit.raw,)


def _narrow_by_memory(
    scored: list[ScoredJob], query: SearchQuery
) -> tuple[list[ScoredJob], int, int]:
    """Apply what memory knows, and count what that cost.

    Both filters are skipped entirely when memory could not answer, which is why `history` being
    None is checked rather than treated as "never seen". Honouring `new_only` over an unreadable
    journal would print an empty list that reads as "nothing new this week", and that is the one
    lie that costs a seeker a job without their ever learning it was told.
    """
    kept: list[ScoredJob] = []
    dismissed_hidden = 0
    not_new_hidden = 0
    for job in scored:
        history = job.history
        if history is not None:
            if history.decision is PostingDecision.DISMISSED and not query.include_dismissed:
                dismissed_hidden += 1
                continue
            if query.new_only and not history.is_new:
                not_new_hidden += 1
                continue
        kept.append(job)
    return kept, dismissed_hidden, not_new_hidden

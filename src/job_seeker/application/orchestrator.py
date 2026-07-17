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
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from job_seeker.application.ports import JobSource
from job_seeker.domain.models import (
    EligibilityStatus,
    Job,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
    SourceResult,
)
from job_seeker.domain.profile import Profile
from job_seeker.domain.services import Deduplicator, EligibilityClassifier, ProfileScorer

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
        scorer: ProfileScorer,
        classifier: EligibilityClassifier,
        profile: Profile,
    ) -> None:
        self._sources = sources
        self._dedup = deduplicator
        self._scorer = scorer
        self._classifier = classifier
        self._profile = profile

    @classmethod
    def default(cls, sources: list[JobSource], profile: Profile) -> JobSeeker:
        """Wire the standard domain services from a profile. The composition root's entry point."""
        return cls(
            sources=sources,
            deduplicator=Deduplicator(),
            scorer=ProfileScorer(profile),
            classifier=EligibilityClassifier(profile),
            profile=profile,
        )

    def run(self, query: SearchQuery) -> SearchResult:
        """Fan out, dedupe, score, classify, filter, rank. Returns jobs plus honest coverage."""
        source_results = self._fetch_all(query)
        collected = [job for result in source_results for job in result.jobs]
        fresh = [job for job in collected if _within_age(job.posted_at, query.max_age_days)]
        deduped = self._dedup.dedupe(fresh)
        suitable = [scored for job in deduped if (scored := self._evaluate(job)) is not None]
        ranked = sorted(suitable, key=lambda scored: scored.fit.value, reverse=True)
        return SearchResult(
            query=query, jobs=ranked, coverage=self._coverage(source_results, ranked)
        )

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

    def _evaluate(self, job: Job) -> ScoredJob | None:
        """Score and classify one job; return a ScoredJob, or None if the seeker cannot hold it."""
        fit = self._scorer.score(job)
        eligibility = self._classifier.classify(job)
        scored = ScoredJob(job=job, fit=fit, eligibility=eligibility)
        return scored if self._is_suitable(scored) else None

    def _is_suitable(self, scored: ScoredJob) -> bool:
        eligibility = scored.eligibility
        if not eligibility.is_eligible:
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

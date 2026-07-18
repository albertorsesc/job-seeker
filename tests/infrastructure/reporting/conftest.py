"""A realistic SearchResult fixture for the reporter tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_seeker.domain.models import (
    Eligibility,
    EligibilityStatus,
    FitScore,
    Job,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
)


@pytest.fixture
def result() -> SearchResult:
    jobs = [
        ScoredJob(
            job=Job(
                title="Senior AI Engineer",
                company="Acme",
                url="https://himalayas.app/jobs/acme-ai",
                source="himalayas",
                salary="USD 150,000 - 180,000",
                posted_at=datetime(2026, 7, 10, tzinfo=UTC),
            ),
            fit=FitScore(value=0.83, raw=6, matched={r"\bpython\b": 3, r"\brag\b": 2}),
            eligibility=Eligibility(
                status=EligibilityStatus.GLOBAL, reason="open to applicants anywhere"
            ),
        ),
        ScoredJob(
            job=Job(
                title="Backend Engineer <script>",  # a title that must be HTML-escaped
                company="Globex & Co",
                url="https://himalayas.app/jobs/globex-be",
                source="himalayas",
            ),
            fit=FitScore(value=0.5, raw=3, matched={r"\bpython\b": 3}),
            eligibility=Eligibility(
                status=EligibilityStatus.REGIONAL, reason="open in your region (LATAM)"
            ),
        ),
    ]
    return SearchResult(
        query=SearchQuery(terms=["AI Engineer"], max_age_days=30),
        jobs=jobs,
        coverage=[SourceCoverage(source="himalayas", scanned=60, kept=2, truncated=True)],
    )

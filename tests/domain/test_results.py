"""Covers the run-result models in `job_seeker.domain.models`.

These exist so a partial run can never pass for a healthy one. That claim is only worth
anything if the edges are pinned, because every failure mode here is silent: the caller gets a
plausible-looking answer and no signal that it is wrong.
"""

from __future__ import annotations

from job_seeker.domain.models import (
    SearchQuery,
    SearchResult,
    SourceCoverage,
    SourceResult,
)


class TestSearchResultIsComplete:
    def test_a_run_where_no_source_executed_is_not_complete(self) -> None:
        """The regression: `any([])` is False, so an unguarded `not any(...)` calls the most
        incomplete run possible complete, and it looks exactly like "no jobs matched"."""
        assert SearchResult(query=SearchQuery()).is_complete is False

    def test_a_run_where_every_source_ran_fully_is_complete(self) -> None:
        result = SearchResult(
            query=SearchQuery(),
            coverage=[
                SourceCoverage(source="himalayas", scanned=100, kept=3),
                SourceCoverage(source="remotive", scanned=40, kept=1),
            ],
        )
        assert result.is_complete is True

    def test_one_failed_source_makes_the_run_incomplete(self) -> None:
        result = SearchResult(
            query=SearchQuery(),
            coverage=[
                SourceCoverage(source="himalayas", scanned=100, kept=3),
                SourceCoverage(source="remoteok", error="HTTP 503"),
            ],
        )
        assert result.is_complete is False

    def test_a_truncated_scan_makes_the_run_incomplete(self) -> None:
        """Truncation is not failure, but it is not completeness either: a budget stopped the
        scan, so "the best jobs" means "the best of what fitted in the budget"."""
        result = SearchResult(
            query=SearchQuery(),
            coverage=[SourceCoverage(source="himalayas", scanned=1000, kept=12, truncated=True)],
        )
        assert result.is_complete is False


class TestFailed:
    def test_a_source_with_an_error_is_failed(self) -> None:
        assert SourceResult(source="remoteok", error="HTTP 503").failed is True

    def test_a_source_without_an_error_is_not_failed(self) -> None:
        assert SourceResult(source="remoteok", scanned=10).failed is False

    def test_coverage_reports_failure_the_same_way_a_result_does(self) -> None:
        """Both inherit SourceOutcome, so the two views of a run cannot drift apart."""
        assert SourceCoverage(source="remoteok", error="HTTP 503").failed is True


class TestDerivedValuesCrossTheWire:
    """An MCP agent receives serialized output and cannot call a Python property.

    Every verdict the domain computes has to survive `model_dump`, or each consumer
    reimplements the domain rule and they drift.
    """

    def test_is_complete_is_serialized(self) -> None:
        assert "is_complete" in SearchResult(query=SearchQuery()).model_dump()

    def test_failed_is_serialized(self) -> None:
        assert SourceResult(source="x").model_dump()["failed"] is False

    def test_is_complete_survives_a_json_round_trip(self) -> None:
        result = SearchResult(
            query=SearchQuery(), coverage=[SourceCoverage(source="x", scanned=1, kept=1)]
        )
        assert '"is_complete":true' in result.model_dump_json().replace(" ", "")

    def test_the_serialization_schema_advertises_the_verdict(self) -> None:
        """`computed_field` puts it in the schema, so the MCP tool contract documents it."""
        schema = SearchResult.model_json_schema(mode="serialization")
        assert "is_complete" in schema["properties"]

"""Covers `job_seeker.infrastructure.sources.remoteok`.

RemoteOK is the first source with NO structured eligibility data, so its jobs arrive with hints of
None and the classifier runs the text path against the location and description. The fixture mirrors
the real API: a legal-boilerplate element 0 that must be skipped, then jobs keyed on `position` /
`apply_url` / `epoch`, with a free-text `location` and no restriction fields. No network: respx.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import respx

from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources.remoteok import RemoteOkSource

API = "https://remoteok.com/api"

_BOILERPLATE = {"legal": "API Terms of Service: link back to Remote OK", "last_updated": 1784311668}


def _epoch(days_ago: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp())


def _job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "1134990",
        "position": "AI Engineer",
        "company": "Acme",
        "apply_url": "https://remoteOK.com/remote-jobs/ai-engineer-acme-1134990",
        "url": "https://remoteOK.com/remote-jobs/ai-engineer-acme-1134990",
        "description": "Build <strong>RAG</strong> systems.<br>Remote role.",
        "location": "Worldwide",
        "epoch": _epoch(1),
        "salary_min": 120000,
        "salary_max": 160000,
        "tags": ["dev", "ai", "machine learning"],
    }
    return {**base, **overrides}


def _payload(*jobs: dict[str, Any]) -> list[dict[str, Any]]:
    return [_BOILERPLATE, *jobs]


def _source() -> RemoteOkSource:
    return RemoteOkSource()


def _fetch(
    jobs: list[dict[str, Any]], *, max_results: int = 50, max_age_days: int | None = None
) -> SourceResult:
    with respx.mock:
        respx.get(API).mock(return_value=httpx.Response(200, json=_payload(*jobs)))
        return _source().fetch(
            SearchQuery(max_results_per_source=max_results, max_age_days=max_age_days)
        )


class TestNormalization:
    def test_skips_the_legal_boilerplate_element(self) -> None:
        result = _fetch([_job()])
        assert len(result.jobs) == 1
        assert result.jobs[0].title == "AI Engineer"

    def test_maps_the_core_fields(self) -> None:
        job = _fetch([_job()]).jobs[0]
        assert job.title == "AI Engineer"
        assert job.company == "Acme"
        assert job.source == "remoteok"
        assert job.url.endswith("-1134990")
        assert job.description == "Build RAG systems. Remote role."  # HTML cleaned
        assert job.location == "Worldwide"

    def test_has_no_structured_hints_so_eligibility_falls_back_to_text(self) -> None:
        """RemoteOK reports no restrictions, so hints are None (the board said nothing), which is
        exactly the case that sends the classifier to the text path."""
        hints = _fetch([_job()]).jobs[0].hints
        assert hints.location_restrictions is None
        assert hints.timezone_restrictions is None

    def test_posted_at_is_tz_aware_from_the_epoch(self) -> None:
        job = _fetch([_job()]).jobs[0]
        assert job.posted_at is not None and job.posted_at.tzinfo is not None

    def test_a_record_without_a_title_or_url_is_skipped(self) -> None:
        result = _fetch([_job(position=""), _job(apply_url="", url="")])
        assert result.jobs == []
        assert not result.failed

    def test_a_zero_salary_renders_as_empty(self) -> None:
        job = _fetch([_job(salary_min=0, salary_max=0)]).jobs[0]
        assert job.salary == ""


class TestBudgetAndFreshness:
    def test_stops_at_max_results_and_marks_truncated(self) -> None:
        jobs = [_job(id=str(i), position=f"Engineer {i}") for i in range(10)]
        result = _fetch(jobs, max_results=4)
        assert len(result.jobs) == 4
        assert result.truncated is True

    def test_drops_records_older_than_the_age_window(self) -> None:
        result = _fetch(
            [_job(position="fresh", epoch=_epoch(2)), _job(position="stale", epoch=_epoch(90))],
            max_age_days=30,
        )
        assert [j.title for j in result.jobs] == ["fresh"]

    def test_scanned_counts_records_excluding_the_boilerplate(self) -> None:
        result = _fetch([_job(id="1"), _job(id="2")])
        assert result.scanned == 2


class TestRobustness:
    def test_an_http_error_is_reported_not_raised(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(500))
            result = _source().fetch(SearchQuery())
        assert result.failed
        assert result.source == "remoteok"

    def test_a_malformed_record_does_not_crash_the_fetch(self) -> None:
        with respx.mock:
            respx.get(API).mock(
                return_value=httpx.Response(200, json=[_BOILERPLATE, "not a dict", _job()])
            )
            result = _source().fetch(SearchQuery(max_age_days=None))
        assert not result.failed
        assert [j.title for j in result.jobs] == ["AI Engineer"]

    def test_a_non_list_payload_is_handled(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
            result = _source().fetch(SearchQuery())
        assert result.jobs == []
        assert not result.failed


class TestAvailability:
    def test_is_always_available_and_does_no_io(self) -> None:
        with respx.mock:
            route = respx.get(API).mock(return_value=httpx.Response(200, json=_payload()))
            assert _source().is_available() is True
            assert route.call_count == 0

    def test_name_is_the_registry_key(self) -> None:
        assert _source().name == "remoteok"

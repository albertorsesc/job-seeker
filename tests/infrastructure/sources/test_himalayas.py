"""Covers `job_seeker.infrastructure.sources.himalayas`.

The page fixtures mirror the real API shape captured from a live request: `companyName` is the
useless literal "name" (the slug is the real identifier), `seniority` is a list, timezone
restrictions are ints, and the restriction keys are always present. No network: respx mocks the
API, and the inter-page delay is zeroed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources.himalayas import HimalayasSource

API = "https://himalayas.app/jobs/api"


def _epoch(days_ago: float) -> int:
    return int((datetime.now(UTC) - timedelta(days=days_ago)).timestamp())


def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "title": "AI Engineer",
        "companyName": "name",  # the API's real, useless value
        "companySlug": "acme-labs",
        "applicationLink": "https://himalayas.app/companies/acme-labs/jobs/ai-engineer",
        "guid": "https://himalayas.app/companies/acme-labs/jobs/ai-engineer",
        "description": "<p>Build <strong>RAG</strong> systems.</p>",
        "excerpt": "Build RAG systems.",
        "employmentType": "Full Time",
        "seniority": ["Senior"],
        "minSalary": 120000,
        "maxSalary": 160000,
        "currency": "USD",
        "pubDate": _epoch(1),
        "locationRestrictions": ["United States"],
        "timezoneRestrictions": [-8, -7, -6, -5],
    }
    return {**base, **overrides}


def _page(records: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    return {
        "jobs": records,
        "totalCount": total if total is not None else len(records),
        "offset": 0,
    }


def _source() -> HimalayasSource:
    return HimalayasSource(page_delay=0.0, sleep=lambda _: None)


def _fetch(
    records: list[dict[str, Any]],
    *,
    max_results: int = 50,
    max_age_days: int | None = None,
    total: int | None = None,
) -> SourceResult:
    with respx.mock:
        respx.get(API).mock(return_value=httpx.Response(200, json=_page(records, total=total)))
        return _source().fetch(
            SearchQuery(max_results_per_source=max_results, max_age_days=max_age_days)
        )


class TestNormalization:
    def test_maps_the_core_fields(self) -> None:
        job = _fetch([_record()]).jobs[0]
        assert job.title == "AI Engineer"
        assert job.source == "himalayas"
        assert job.url.endswith("/ai-engineer")
        assert job.description == "Build RAG systems."  # HTML cleaned

    def test_company_comes_from_the_slug_because_companyName_is_junk(self) -> None:
        """Verified against the live API: companyName is the literal "name" for every record, so
        the slug is the only real identifier. Prettified for display."""
        job = _fetch([_record(companySlug="m-kopa")]).jobs[0]
        assert job.company == "M Kopa"

    def test_a_real_company_name_is_preferred_when_present(self) -> None:
        """Defends against the day the API is fixed: a genuine name wins over the slug."""
        job = _fetch([_record(companyName="Acme Labs, Inc.")]).jobs[0]
        assert job.company == "Acme Labs, Inc."

    def test_structured_restrictions_become_hints(self) -> None:
        job = _fetch([_record()]).jobs[0]
        assert job.hints.location_restrictions == ("United States",)
        assert job.hints.timezone_restrictions == (-8.0, -7.0, -6.0, -5.0)

    def test_a_worldwide_posting_has_empty_not_none_restrictions(self) -> None:
        """Himalayas always reports the field, so an open role is `()` (said: no restriction),
        never `None` (said nothing). That distinction is the whole point of EligibilityHints."""
        job = _fetch([_record(locationRestrictions=[], timezoneRestrictions=[])]).jobs[0]
        assert job.hints.location_restrictions == ()
        assert job.hints.timezone_restrictions == ()

    def test_posted_at_is_timezone_aware(self) -> None:
        job = _fetch([_record()]).jobs[0]
        assert job.posted_at is not None
        assert job.posted_at.tzinfo is not None

    def test_seniority_list_is_flattened_to_text(self) -> None:
        job = _fetch([_record(seniority=["Senior", "Lead"])]).jobs[0]
        assert job.seniority == "Senior, Lead"

    def test_a_record_without_a_title_or_url_is_skipped_not_crashed(self) -> None:
        result = _fetch([_record(title=""), _record(applicationLink="", guid="")])
        assert result.jobs == []
        assert not result.failed


class TestPaginationAndBudget:
    def test_stops_at_max_results_and_marks_truncated(self) -> None:
        result = _fetch([_record(title=f"Role {i}") for i in range(20)], max_results=5, total=100)
        assert len(result.jobs) == 5
        assert result.truncated is True

    def test_walks_pages_until_it_has_enough_and_advances_the_offset(self) -> None:
        full = [_record(title=f"Role {i}") for i in range(20)]
        with respx.mock:
            route = respx.get(API).mock(
                side_effect=[
                    httpx.Response(200, json={"jobs": full, "totalCount": 40, "offset": 0}),
                    httpx.Response(200, json={"jobs": full, "totalCount": 40, "offset": 20}),
                ]
            )
            result = _source().fetch(SearchQuery(max_results_per_source=30, max_age_days=None))
        assert len(result.jobs) == 30
        # Prove the state machine actually paged: the second request asked for offset 20. A test
        # that only counts jobs would pass even if offset never moved.
        assert route.calls[0].request.url.params["offset"] == "0"
        assert route.calls[1].request.url.params["offset"] == "20"

    def test_a_broken_api_returning_full_pages_forever_is_bounded_by_the_scan_cap(self) -> None:
        """The scan cap is the only guard against an API that never signals an end. Nothing else
        exercises it, so a regression that weakened it would pass every other test."""
        full = [_record(title=f"Role {i}") for i in range(20)]
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(200, json=_page(full, total=10**9)))
            source = HimalayasSource(page_delay=0.0, sleep=lambda _: None, scan_cap=60)
            result = source.fetch(SearchQuery(max_results_per_source=1000, max_age_days=None))
        assert result.scanned <= 80  # stopped near the cap, did not walk a billion records
        assert result.truncated is True

    def test_a_full_page_entirely_out_of_the_age_window_stops_the_scan(self) -> None:
        """Recency ordering: once a whole page is stale, everything after it is older, so the
        scan stops there rather than paging to the end of a six-figure feed."""
        stale_page = [_record(title=f"old {i}", pubDate=_epoch(200)) for i in range(20)]
        with respx.mock:
            route = respx.get(API).mock(
                return_value=httpx.Response(200, json=_page(stale_page, total=10**6))
            )
            result = _source().fetch(SearchQuery(max_results_per_source=50, max_age_days=30))
        assert result.jobs == []
        assert result.truncated is False
        assert route.call_count == 1  # did not page past the first all-stale page

    def test_a_200_with_an_html_body_is_reported_not_raised(self) -> None:
        """Boards return 200 + an HTML challenge or maintenance page under load. json() raising
        must become a reported failure, not an exception out of a thread-pool worker."""
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(200, text="<html>Just a moment</html>"))
            result = _source().fetch(SearchQuery())
        assert result.failed
        assert result.jobs == []

    def test_filling_the_result_on_a_short_final_page_is_not_truncated(self) -> None:
        """If the last (short) page is fully consumed and exactly fills the result, nothing
        remains, so the run is complete, not truncated."""
        result = _fetch([_record(title=f"Role {i}") for i in range(3)], max_results=3, total=3)
        assert len(result.jobs) == 3
        assert result.truncated is False

    def test_an_empty_page_ends_the_scan_cleanly(self) -> None:
        result = _fetch([], max_results=50)
        assert result.jobs == []
        assert result.truncated is False
        assert not result.failed

    def test_records_older_than_the_age_window_are_dropped(self) -> None:
        result = _fetch(
            [_record(title="fresh", pubDate=_epoch(3)), _record(title="stale", pubDate=_epoch(90))],
            max_age_days=30,
        )
        titles = [j.title for j in result.jobs]
        assert "fresh" in titles
        assert "stale" not in titles

    def test_scanned_reflects_records_examined(self) -> None:
        result = _fetch([_record(), _record()], max_results=50)
        assert result.scanned == 2


class TestMalformedRecordsNeverRaise:
    """`fetch` runs in a ThreadPoolExecutor worker and must not raise, ever. A board returning
    HTTP 200 with structurally wrong JSON is an expected hazard, not an exception: the httpx
    error catch does not cover a `float("abc")` or a `.get` on a non-dict, so normalization has
    to be resilient on its own. Each case here is JSON a real or hostile board could send.
    """

    @pytest.mark.parametrize(
        "overrides",
        [
            pytest.param({"timezoneRestrictions": ["abc", -5]}, id="non-numeric-timezone"),
            pytest.param({"timezoneRestrictions": [None, -5]}, id="null-timezone-value"),
            pytest.param({"minSalary": "120k", "maxSalary": "150k"}, id="string-salary"),
            pytest.param({"seniority": "Senior"}, id="seniority-not-a-list"),
            pytest.param({"locationRestrictions": "United States"}, id="location-not-a-list"),
            pytest.param({"pubDate": "yesterday"}, id="non-numeric-pubdate"),
        ],
    )
    def test_a_malformed_field_does_not_raise(self, overrides: dict[str, Any]) -> None:
        result = _fetch([_record(**overrides)])
        assert not result.failed  # a SourceResult came back; fetch did not raise

    def test_a_non_dict_record_is_skipped_not_crashed(self) -> None:
        with respx.mock:
            respx.get(API).mock(
                return_value=httpx.Response(
                    200, json={"jobs": ["i am a string, not a record", None], "totalCount": 2}
                )
            )
            result = _source().fetch(SearchQuery(max_age_days=None))
        assert not result.failed
        assert result.jobs == []

    def test_a_repairable_record_survives_with_the_bad_element_dropped(self) -> None:
        """A single bad value does not discard the whole posting: the record is kept, the
        unusable timezone entry is dropped, and the field stays a tuple (the board reported it)."""
        job = _fetch([_record(timezoneRestrictions=["nope", -5, None, -6])]).jobs[0]
        assert job.hints.timezone_restrictions == (-5.0, -6.0)

    def test_an_unusable_record_does_not_drop_the_good_ones_beside_it(self) -> None:
        with respx.mock:
            respx.get(API).mock(
                return_value=httpx.Response(
                    200,
                    json={"jobs": ["not a record at all", _record(title="good")], "totalCount": 2},
                )
            )
            result = _source().fetch(SearchQuery(max_age_days=None))
        assert [j.title for j in result.jobs] == ["good"]


class TestFailureIsReportedNotRaised:
    def test_a_500_becomes_an_error_result_not_an_exception(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(500))
            result = _source().fetch(SearchQuery())
        assert result.failed
        assert result.jobs == []
        assert result.source == "himalayas"

    def test_a_network_error_becomes_an_error_result(self) -> None:
        with respx.mock:
            respx.get(API).mock(side_effect=httpx.ConnectError("boom"))
            result = _source().fetch(SearchQuery())
        assert result.failed
        assert result.source == "himalayas"


class TestAvailability:
    def test_is_always_available_and_does_no_io(self) -> None:
        """No credential, no optional dependency. is_available must not touch the network."""
        with respx.mock:
            route = respx.get(API).mock(return_value=httpx.Response(200, json=_page([])))
            assert _source().is_available() is True
            assert route.call_count == 0

    def test_name_is_the_registry_key(self) -> None:
        assert _source().name == "himalayas"

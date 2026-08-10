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

from job_seeker.domain.models import (
    CurrencySource,
    SalaryPeriod,
    SearchQuery,
    SourceResult,
)
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
            SearchQuery(scan_depth_per_source=max_results, max_age_days=max_age_days)
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

    def test_the_board_figures_survive_as_numbers(self) -> None:
        """The adapter's job is to carry what the board published, not to render it."""
        salary = _fetch([_record(minSalary=120000, maxSalary=160000)]).jobs[0].salary
        assert salary is not None
        assert (salary.minimum, salary.maximum, salary.currency) == (120000, 160000, "USD")

    def test_a_floor_with_no_ceiling_leaves_the_ceiling_unknown(self) -> None:
        """Not the same fact as a fixed rate, and it must not become a range against a phantom
        zero."""
        salary = _fetch([_record(minSalary=120000, maxSalary=None)]).jobs[0].salary
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (120000, None)

    def test_a_ceiling_with_no_floor_leaves_the_floor_unknown(self) -> None:
        salary = _fetch([_record(minSalary=None, maxSalary=160000)]).jobs[0].salary
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (None, 160000)

    def test_a_zero_figure_is_treated_as_unspecified_not_as_a_salary_of_zero(self) -> None:
        salary = _fetch([_record(minSalary=0, maxSalary=0)]).jobs[0].salary
        assert salary is None

    def test_a_posting_with_no_pay_fields_carries_no_salary_at_all(self) -> None:
        """None, not an empty range: the board said nothing, which is not the same as saying
        there is no pay."""
        assert _fetch([_record(minSalary=None, maxSalary=None)]).jobs[0].salary is None

    def test_an_obviously_hourly_figure_is_declared_hourly(self) -> None:
        """From live data: 47 of 311 pay-bearing records sit below 200, the largest being 150, and
        their titles are hourly work. No annual salary is 150 in any currency."""
        salary = _fetch([_record(minSalary=85, maxSalary=85)]).jobs[0].salary
        assert salary is not None
        assert salary.period is SalaryPeriod.HOUR
        assert salary.annual_minimum == 176_800  # 85 x 2080, comparable at last

    def test_an_obviously_annual_figure_is_declared_annual(self) -> None:
        """243 of 311 records sit at or above 50,000, the smallest being exactly 50,000."""
        salary = _fetch([_record(minSalary=146_000, maxSalary=225_000)]).jobs[0].salary
        assert salary is not None
        assert salary.period is SalaryPeriod.YEAR
        assert salary.annual_minimum == 146_000

    def test_the_ambiguous_middle_is_declared_unknown_rather_than_guessed(self) -> None:
        """The band this adapter refuses to guess in. Live records there are genuinely mixed:
        3,255-4,160 USD is a Colombian monthly salary, 47,000-67,200 USD is annual, and magnitude
        cannot separate them. An unknown period yields no annualized figure, which is the honest
        answer rather than a confident wrong one.
        """
        salary = _fetch([_record(minSalary=3255, maxSalary=4160)]).jobs[0].salary
        assert salary is not None
        assert salary.period is None
        assert salary.annual_minimum is None
        assert salary.minimum == 3255  # the board's own figure is still reported

    def test_the_currency_is_marked_published_because_this_board_states_it(self) -> None:
        salary = _fetch([_record(currency="CAD")]).jobs[0].salary
        assert salary is not None
        assert (salary.currency, salary.currency_source) == ("CAD", CurrencySource.PUBLISHED)

    def test_a_posting_with_no_currency_carries_neither_currency_nor_a_source(self) -> None:
        """Absent, not empty. A currency with no stated origin cannot be told apart from one the
        engine invented, which is exactly what the source field exists to prevent."""
        salary = _fetch([_record(currency="")]).jobs[0].salary
        assert salary is not None
        assert salary.currency is None
        assert salary.currency_source is None

    def test_the_annual_band_is_not_applied_to_an_uncalibrated_currency(self) -> None:
        """The upper band is a magnitude, so it only means anything where it was measured. A
        monthly 3,000,000 COP clears 50,000 and would read as annual, so an uncalibrated currency
        falls through to unknown rather than being confidently mislabelled.
        """
        salary = _fetch([_record(minSalary=3_000_000, maxSalary=4_000_000, currency="COP")])
        pay = salary.jobs[0].salary
        assert pay is not None
        assert pay.period is None
        assert pay.annual_minimum is None

    def test_the_hourly_band_applies_in_any_currency(self) -> None:
        """Safe universally: no annual salary anywhere is under 200 units."""
        pay = _fetch([_record(minSalary=150, maxSalary=150, currency="COP")]).jobs[0].salary
        assert pay is not None
        assert pay.period is SalaryPeriod.HOUR

    def test_an_inverted_range_is_kept_as_text_rather_than_ending_the_page(self) -> None:
        """A board can contradict itself. SalaryRange refuses an inverted range, so letting it
        construct would raise inside normalization and take the whole page with it. Swapping the
        bounds instead would report a fact the board never published.
        """
        result = _fetch([_record(minSalary=200000, maxSalary=100000)])
        salary = result.jobs[0].salary
        assert not result.failed
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (None, None)
        assert "inverted range" in salary.note and "withheld" in salary.note

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
            result = _source().fetch(SearchQuery(scan_depth_per_source=30, max_age_days=None))
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
            result = source.fetch(SearchQuery(scan_depth_per_source=1000, max_age_days=None))
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
            result = _source().fetch(SearchQuery(scan_depth_per_source=50, max_age_days=30))
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

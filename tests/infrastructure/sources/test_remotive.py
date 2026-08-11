"""Covers `job_seeker.infrastructure.sources.remotive`.

Remotive is the third board and the first that states pay as prose. Every salary string in
`TestSalaryText` is a real value captured from the live API, which is the only reason the parser
is trusted to read them: they are the formats the board actually emits, not formats it might.

No network: respx mocks the API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from job_seeker.domain.models import CurrencySource, SalaryPeriod, SearchQuery, SourceResult
from job_seeker.infrastructure.sources.remotive import RemotiveSource, _salary

API = "https://remotive.com/api/remote-jobs"


def _job(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 2086540,
        "url": "https://remotive.com/remote-jobs/software-dev/ai-engineer-2086540",
        "title": "AI Engineer",
        "company_name": "Acme",
        "category": "Software Development",
        "tags": ["python", "llm"],
        "job_type": "full_time",
        "publication_date": (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        "candidate_required_location": "Worldwide",
        "salary": "$150k - $230k",
        "description": "<p>Build <strong>RAG</strong> systems.</p>",
    }
    return {**base, **overrides}


def _payload(*jobs: dict[str, Any]) -> dict[str, Any]:
    return {
        "0-legal-notice": "Hey, thanks for using Remotive's API",
        "job-count": len(jobs),
        "total-job-count": len(jobs),
        "jobs": list(jobs),
    }


def _fetch(jobs: list[dict[str, Any]], **query: Any) -> SourceResult:
    with respx.mock:
        respx.get(API).mock(return_value=httpx.Response(200, json=_payload(*jobs)))
        return RemotiveSource().fetch(SearchQuery(max_age_days=None, **query))


class TestNormalization:
    def test_maps_the_core_fields(self) -> None:
        job = _fetch([_job()]).jobs[0]
        assert job.title == "AI Engineer"
        assert job.company == "Acme"
        assert job.source == "remotive"
        assert job.url.endswith("-2086540")
        assert job.description == "Build RAG systems."  # HTML cleaned

    def test_the_iso_publication_date_becomes_an_aware_datetime(self) -> None:
        """Remotive publishes "2026-08-08T21:48:06", naive ISO, where the other boards send epoch
        seconds. A naive datetime compared against an aware `now` raises during age filtering."""
        job = _fetch([_job(publication_date="2026-08-08T21:48:06")]).jobs[0]
        assert job.posted_at is not None
        assert job.posted_at.tzinfo is not None
        assert job.posted_at.year == 2026

    def test_an_unparseable_date_is_absent_rather_than_fatal(self) -> None:
        result = _fetch([_job(publication_date="not a date")])
        assert result.jobs[0].posted_at is None
        assert not result.failed

    def test_the_employment_type_is_carried_readably(self) -> None:
        assert _fetch([_job(job_type="part_time")]).jobs[0].employment_type == "part time"

    def test_a_record_without_a_title_or_url_is_skipped(self) -> None:
        result = _fetch([_job(title=""), _job(url="")])
        assert result.jobs == []
        assert not result.failed


class TestEligibilityHints:
    """The reason this board is worth having. `candidate_required_location` is a comma-separated
    place list, so Remotive joins Himalayas as a board with structured eligibility rather than one
    the classifier has to guess about from prose.
    """

    def test_a_place_list_becomes_structured_restrictions(self) -> None:
        job = _fetch([_job(candidate_required_location="Americas, Europe, Israel")]).jobs[0]
        assert job.hints.location_restrictions == ("Americas", "Europe", "Israel")

    def test_worldwide_is_carried_through_as_stated(self) -> None:
        """The classifier already treats "worldwide" as open to all; the adapter must not
        pre-interpret it, only report what the board said."""
        job = _fetch([_job(candidate_required_location="Worldwide")]).jobs[0]
        assert job.hints.location_restrictions == ("Worldwide",)

    def test_an_empty_location_means_the_board_said_nothing(self) -> None:
        """None, not (): the difference between "no restriction" and "no statement" is the whole
        eligibility design, and an empty string is the absence of a claim."""
        job = _fetch([_job(candidate_required_location="")]).jobs[0]
        assert job.hints.location_restrictions is None

    def test_the_board_publishes_no_timezone_restrictions(self) -> None:
        job = _fetch([_job()]).jobs[0]
        assert job.hints.timezone_restrictions is None


class TestSalaryText:
    """Every string here was captured from the live API on 2026-08-10.

    The board states the period in the text, which makes it more reliable than the magnitude
    inference Himalayas needs. Anything not confidently readable keeps its original text in `note`
    and reports no figures, because a wrong salary is worse than an unparsed one.
    """

    @pytest.mark.parametrize(
        "text,minimum,maximum,period",
        [
            pytest.param("$150k - $230k", 150_000, 230_000, SalaryPeriod.YEAR, id="k-range"),
            pytest.param("$170k - $200k", 170_000, 200_000, SalaryPeriod.YEAR, id="k-range-2"),
            pytest.param("$36k", 36_000, None, SalaryPeriod.YEAR, id="k-single"),
            pytest.param("$20k -$35k", 20_000, 35_000, SalaryPeriod.YEAR, id="k-range-tight"),
            pytest.param("$45,000 - $50,000", 45_000, 50_000, SalaryPeriod.YEAR, id="grouped"),
            pytest.param("$50-$75 /hour", 50, 75, SalaryPeriod.HOUR, id="hourly-range"),
            pytest.param("$17/hr", 17, None, SalaryPeriod.HOUR, id="hourly-single"),
            pytest.param("$90 - $150 /hour", 90, 150, SalaryPeriod.HOUR, id="hourly-spaced"),
        ],
    )
    def test_a_readable_figure_is_parsed_with_its_period(
        self, text: str, minimum: float, maximum: float | None, period: SalaryPeriod
    ) -> None:
        salary = _salary(text)
        assert salary is not None
        assert (salary.minimum, salary.maximum, salary.period) == (minimum, maximum, period)
        assert salary.note == text  # the board's own words always survive

    def test_an_ote_figure_reports_no_base_salary(self) -> None:
        """ "OTE $25k - $35k" is on-target earnings, commission included. Reading it as base pay
        would overstate the job, so the text is kept and the figures are not claimed."""
        salary = _salary("OTE $25k - $35k")
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (None, None)
        assert salary.note == "OTE $25k - $35k"

    def test_an_ambiguous_decimal_comma_is_not_guessed_at(self) -> None:
        """ "$31,2k" is European notation for 31.2k. Read as a thousands separator it becomes
        312k, an order of magnitude out, so it is left unparsed."""
        salary = _salary("$31,2k- $52k")
        assert salary is not None
        assert salary.minimum is None
        assert salary.note == "$31,2k- $52k"

    def test_no_salary_text_means_no_salary_at_all(self) -> None:
        assert _salary("") is None
        assert _salary("   ") is None

    def test_unreadable_prose_is_kept_as_a_note(self) -> None:
        salary = _salary("Competitive, DOE")
        assert salary is not None
        assert salary.note == "Competitive, DOE"
        assert salary.minimum is None

    def test_the_currency_is_marked_assumed_because_the_board_only_writes_a_dollar_sign(
        self,
    ) -> None:
        """A bare "$" is not a currency. The board is US-centric so USD is the adapter's inference,
        and it says so rather than passing an assumption off as a published fact."""
        salary = _salary("$150k - $230k")
        assert salary is not None
        assert (salary.currency, salary.currency_source) == ("USD", CurrencySource.ASSUMED)

    def test_an_inverted_range_does_not_end_the_fetch(self) -> None:
        result = _fetch([_job(salary="$200k - $100k")])
        assert not result.failed
        assert result.jobs[0].salary is not None


class TestCoverageIsHonestAboutTheTwentyJobCap:
    """The board's API returns 20 postings and reports `total-job-count: 20`, whatever is asked of
    it, while the site itself lists thousands. Reporting a complete scan of 20 would be the exact
    lie `SourceCoverage` exists to prevent.
    """

    def test_a_full_window_is_always_reported_truncated(self) -> None:
        result = _fetch([_job(id=i, url=f"https://remotive.com/j/{i}") for i in range(20)])
        assert result.scanned == 20
        assert result.truncated is True

    def test_it_stays_truncated_even_when_the_budget_is_not_reached(self) -> None:
        """Not "we stopped early" but "the board only ever shows a window", so the flag does not
        depend on our own scan depth."""
        result = _fetch([_job()], scan_depth_per_source=500)
        assert result.truncated is True


class TestRobustness:
    def test_an_http_error_is_reported_not_raised(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(503))
            result = RemotiveSource().fetch(SearchQuery())
        assert result.failed
        assert result.jobs == []

    def test_a_non_json_body_is_reported_not_raised(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
            assert RemotiveSource().fetch(SearchQuery()).failed

    def test_a_payload_without_a_jobs_key_yields_nothing_rather_than_crashing(self) -> None:
        with respx.mock:
            respx.get(API).mock(return_value=httpx.Response(200, json={"legal": "notice"}))
            result = RemotiveSource().fetch(SearchQuery())
        assert result.jobs == []
        assert not result.failed

    def test_a_non_dict_record_is_skipped(self) -> None:
        with respx.mock:
            respx.get(API).mock(
                return_value=httpx.Response(200, json={"jobs": ["not a job", _job()]})
            )
            result = RemotiveSource().fetch(SearchQuery(max_age_days=None))
        assert len(result.jobs) == 1

    def test_it_is_always_available(self) -> None:
        assert RemotiveSource().is_available() is True


class TestTimezoneBandsAreNotPlaces:
    """The board writes both kinds of value into one comma-separated field. Measured on a live
    window: four of twenty postings carried a band."""

    def test_a_band_becomes_the_offsets_it_covers(self) -> None:
        hints = _fetch([_job(candidate_required_location="USA timezones")]).jobs[0].hints
        assert hints.timezone_restrictions == (-8.0, -7.0, -6.0, -5.0)

    def test_a_band_alone_is_not_reported_as_a_place_restriction(self) -> None:
        """The case that costs a job. Read as a place it matches no country and excludes, and a
        seeker at UTC-6 sits inside this band."""
        hints = _fetch([_job(candidate_required_location="USA timezones")]).jobs[0].hints
        assert hints.location_restrictions is None

    def test_places_and_bands_in_one_list_go_to_their_own_fields(self) -> None:
        hints = (
            _fetch([_job(candidate_required_location="USA, Canada, USA timezones")]).jobs[0].hints
        )
        assert hints.location_restrictions == ("USA", "Canada")
        assert hints.timezone_restrictions == (-8.0, -7.0, -6.0, -5.0)

    def test_a_list_of_places_alone_says_nothing_about_timezones(self) -> None:
        """Not `()`, which would claim the board stated there is no timezone restriction."""
        hints = _fetch([_job(candidate_required_location="Germany, France")]).jobs[0].hints
        assert hints.timezone_restrictions is None

    def test_an_unrecognized_band_keeps_excluding_rather_than_going_quiet(self) -> None:
        hints = _fetch([_job(candidate_required_location="Asian timezones")]).jobs[0].hints
        assert hints.location_restrictions == ("Asian timezones",)

    def test_two_bands_merge_without_repeating_an_offset(self) -> None:
        hints = (
            _fetch(
                [
                    _job(
                        candidate_required_location="USA timezones, US timezones, European timezones"
                    )
                ]
            )
            .jobs[0]
            .hints
        )
        assert hints.timezone_restrictions == (-8.0, -7.0, -6.0, -5.0, 0.0, 1.0, 2.0)

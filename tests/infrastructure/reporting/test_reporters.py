"""Covers the reporters in `job_seeker.infrastructure.reporting`.

One test module for the three sibling reporters: they share the fixture and the same contract
(render a SearchResult to a string; never filter or reorder).
"""

from __future__ import annotations

import csv
import io
import json

from job_seeker.domain.models import (
    CurrencySource,
    Eligibility,
    EligibilityStatus,
    FitScore,
    Job,
    Relevance,
    SalaryPeriod,
    SalaryRange,
    ScoredJob,
    SearchQuery,
    SearchResult,
    SourceCoverage,
)
from job_seeker.infrastructure.reporting import (
    CsvReporter,
    HtmlReporter,
    JsonReporter,
    reporter_for,
)


def _evil_result() -> SearchResult:
    """A posting a hostile board could serve: a formula-injection title and a javascript: URL."""
    scored = ScoredJob(
        job=Job(
            title="=cmd|'/c calc'!A1",
            company="Acme",
            url="javascript:alert(document.cookie)",
            source="himalayas",
        ),
        fit=FitScore(value=1.0, raw=1, matched={"python": 1}),
        relevance=Relevance(keep=True, reason="title matches 'engineer'"),
        eligibility=Eligibility(status=EligibilityStatus.GLOBAL, reason="ok"),
    )
    return SearchResult(
        query=SearchQuery(),
        jobs=[scored],
        coverage=[SourceCoverage(source="himalayas", kept=1)],
    )


class TestJsonReporter:
    def test_is_valid_json_with_jobs_and_coverage(self, result: SearchResult) -> None:
        data = json.loads(JsonReporter().render(result))
        assert len(data["jobs"]) == 2
        assert data["coverage"][0]["source"] == "himalayas"
        assert data["fully_scanned"] is False

    def test_carries_fit_and_eligibility_per_job(self, result: SearchResult) -> None:
        top = json.loads(JsonReporter().render(result))["jobs"][0]
        assert top["fit"]["value"] == 0.83  # normalized 0.0-1.0, not a raw sum
        assert top["fit"]["raw"] == 6
        assert top["fit"]["matched"] == {r"\bpython\b": 3, r"\brag\b": 2}
        assert top["relevance"]["keep"] is True
        assert top["relevance"]["reason"] == "title matches 'engineer'"
        assert top["eligibility"]["status"] == "global"
        assert top["eligibility"]["reason"]

    def test_preserves_rank_order(self, result: SearchResult) -> None:
        titles = [j["job"]["title"] for j in json.loads(JsonReporter().render(result))["jobs"]]
        assert titles == ["Senior AI Engineer", "Backend Engineer <script>"]


class TestCsvReporter:
    def test_has_a_header_and_one_row_per_job(self, result: SearchResult) -> None:
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert len(rows) == 2
        assert rows[0]["title"] == "Senior AI Engineer"
        assert rows[0]["fit"] == "0.83"  # normalized, comparable across profiles
        assert rows[0]["eligibility"] == "global"

    def test_explains_the_fit_with_a_matched_breakdown(self, result: SearchResult) -> None:
        """The score explains itself: the cell says which signals earned it, so a reader is not
        left guessing what "0.83" came from."""
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert r"\bpython\b +3" in rows[0]["matched"]
        assert r"\brag\b +2" in rows[0]["matched"]
        assert rows[0]["relevance"] == "title matches 'engineer'"

    def test_a_field_with_a_comma_is_quoted_not_split(self, result: SearchResult) -> None:
        """Asserts the whole cell, not a substring.

        A substring check passes even when the field splits, because the fragment before the comma
        still contains it. Only equality against the full comma-bearing value proves the quoting
        held.
        """
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert rows[0]["matched"] == r"\bpython\b +3, \brag\b +2"

    def test_pay_lands_in_sortable_numeric_columns(self, result: SearchResult) -> None:
        """The reason for splitting the column: a spreadsheet can sort on a number and cannot on
        "USD 150,000 - 180,000", and sorting by pay is the first thing anyone does here."""
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert rows[0]["salary_min"] == "150000.0"
        assert rows[0]["salary_max"] == "180000.0"
        assert rows[0]["currency"] == "USD"
        assert rows[0]["salary_note"] == ""

    def test_a_posting_with_no_pay_leaves_the_columns_empty(self, result: SearchResult) -> None:
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert rows[1]["salary_min"] == ""
        assert rows[1]["currency"] == ""

    def test_a_formula_injection_title_is_neutralized(self) -> None:
        """A title starting with = + - @ is a spreadsheet formula. Board data is untrusted, so a
        title like "=cmd|..." must not execute when the CSV is opened."""
        cell = list(csv.DictReader(io.StringIO(CsvReporter().render(_evil_result()))))[0]["title"]
        assert not cell.startswith(("=", "+", "-", "@"))


class TestHtmlReporter:
    def test_is_a_self_contained_html_document(self, result: SearchResult) -> None:
        html = HtmlReporter().render(result)
        assert html.lstrip().startswith("<!doctype html>")
        assert "<style>" in html  # inline CSS, no external assets
        assert "http://" not in html.replace(
            result.jobs[0].job.url, ""
        )  # no external hrefs beyond job links

    def test_shows_each_job_with_its_fit_and_eligibility(self, result: SearchResult) -> None:
        html = HtmlReporter().render(result)
        assert "Senior AI Engineer" in html
        assert "global" in html
        assert "open to applicants anywhere" in html

    def test_shows_fit_as_a_percentage_with_its_breakdown(self, result: SearchResult) -> None:
        """A normalized fit reads as a percentage a human can judge, and the breakdown says which
        signals earned it."""
        html = HtmlReporter().render(result)
        assert "fit 83%" in html
        assert r"\bpython\b +3" in html

    def test_shows_why_each_job_is_on_topic(self, result: SearchResult) -> None:
        """The relevance stage explains itself in the human report too, not only in JSON/CSV."""
        html = HtmlReporter().render(result)
        assert "relevant: title matches" in html  # the apostrophe in the term is HTML-escaped
        assert "engineer" in html

    def test_escapes_html_in_posting_data(self, result: SearchResult) -> None:
        """Posting text is untrusted board data. A title with a tag must not become live markup."""
        html = HtmlReporter().render(result)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "Globex &amp; Co" in html

    def test_reports_coverage_and_completeness(self, result: SearchResult) -> None:
        html = HtmlReporter().render(result)
        assert "himalayas" in html
        assert "60" in html  # scanned

    def test_a_javascript_url_is_not_rendered_as_a_live_link(self) -> None:
        """A board could serve a javascript: apply URL. Clicking it must not execute script, so a
        non-http(s) URL is never placed in an href."""
        html = HtmlReporter().render(_evil_result())
        assert 'href="javascript:' not in html
        assert "javascript:alert" not in html or "&" in html  # if shown, only as escaped text


class TestHtmlSalaryRendering:
    """Formatting moved here from the adapters, so the branches moved here too.

    The fixture carries exactly one two-bound range, so none of these states was reachable from it
    and every branch below shipped untested when the formatting arrived.
    """

    @staticmethod
    def _rendered(salary: SalaryRange | None) -> str:
        result = SearchResult(
            query=SearchQuery(terms=["Engineer"]),
            jobs=[
                ScoredJob(
                    job=Job(
                        title="Engineer",
                        company="Acme",
                        url="https://example.com/j/1",
                        source="himalayas",
                        salary=salary,
                    ),
                    fit=FitScore(value=0.5, raw=3, matched={r"\bpython\b": 3}),
                    relevance=Relevance(keep=True, reason="title matches 'engineer'"),
                    eligibility=Eligibility(status=EligibilityStatus.GLOBAL, reason="open"),
                )
            ],
            coverage=[SourceCoverage(source="himalayas", scanned=1, kept=1)],
        )
        return HtmlReporter().render(result)

    def test_a_two_bound_range_reads_as_a_range(self) -> None:
        html = self._rendered(
            SalaryRange(
                minimum=120_000,
                maximum=160_000,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
            )
        )
        assert "USD 120,000 - 160,000" in html

    def test_equal_bounds_render_once_not_as_a_range(self) -> None:
        """A fixed rate. "USD 150,000 - 150,000" is noise, and the rule was stated in a docstring
        with nothing checking it."""
        html = self._rendered(
            SalaryRange(
                minimum=150_000,
                maximum=150_000,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
            )
        )
        assert "USD 150,000" in html
        assert "150,000 - 150,000" not in html

    def test_a_floor_alone_renders_as_one_figure(self) -> None:
        html = self._rendered(
            SalaryRange(minimum=120_000, currency="USD", currency_source=CurrencySource.PUBLISHED)
        )
        assert "USD 120,000" in html

    def test_a_missing_currency_leaves_no_stray_leading_space(self) -> None:
        html = self._rendered(SalaryRange(minimum=150_000))
        assert "&middot; 150,000" in html

    def test_board_prose_is_shown_when_there_are_no_figures(self) -> None:
        """The entire justification for the `raw` field, and it had no test."""
        html = self._rendered(
            SalaryRange(
                currency="USD", currency_source=CurrencySource.PUBLISHED, note="Competitive, DOE"
            )
        )
        assert "Competitive, DOE" in html

    def test_a_note_renders_without_a_currency_prefix(self) -> None:
        """A note is prose, not an amount, so no currency qualifies it.

        This was the opposite once, and correctly so: the note then held a bare "200,000 -
        100,000", which needed its unit. Now that a withheld range explains itself in words
        instead of restating the figures, prefixing produced "MXN board published an inverted
        range". The two fixes are the same rule, applied to a field that changed shape.
        """
        html = self._rendered(
            SalaryRange(
                currency="MXN",
                currency_source=CurrencySource.PUBLISHED,
                note="board published an inverted range; figures withheld",
            )
        )
        assert "board published an inverted range; figures withheld" in html
        assert "MXN board" not in html

    def test_an_annual_figure_names_its_period_without_a_redundant_conversion(self) -> None:
        html = self._rendered(
            SalaryRange(
                minimum=120_000,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
        )
        assert "USD 120,000 per year" in html
        assert "/year est." not in html  # annualizing an annual figure says nothing

    def test_an_hourly_figure_shows_the_comparable_annual_beside_it(self) -> None:
        """The live posting that motivated this: $85/hour reads as 85 next to a 60,000, so the
        comparable figure has to be visible, marked as the estimate it is."""
        html = self._rendered(
            SalaryRange(
                minimum=85,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.HOUR,
            )
        )
        assert "USD 85 per hour" in html
        assert "176,800" in html
        assert "est." in html  # the full-time assumption is not presented as fact

    def test_an_hourly_range_annualizes_both_bounds(self) -> None:
        html = self._rendered(
            SalaryRange(
                minimum=85,
                maximum=110,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.HOUR,
            )
        )
        assert "USD 85 - 110 per hour" in html
        assert "176,800 - 228,800" in html

    def test_an_unknown_period_claims_nothing(self) -> None:
        html = self._rendered(
            SalaryRange(
                minimum=3255, maximum=4160, currency="USD", currency_source=CurrencySource.PUBLISHED
            )
        )
        assert "USD 3,255 - 4,160" in html
        assert "per " not in html.split('class="meta"')[1].split("</p>")[0]
        assert "est." not in html

    def test_a_posting_with_no_pay_says_nothing_about_pay(self) -> None:
        html = self._rendered(None)
        assert "Acme &middot; himalayas</p>" in html  # no trailing salary separator


class TestAZeroFitJobRenders:
    """A job can match the search and score nothing: relevance and fit are separate stages.

    A live run produced exactly this ("Chief Engineer", fit 0.0), so the empty-breakdown path is
    ordinary output, not an edge case. It had never been rendered in a test.
    """

    @staticmethod
    def _zero_fit() -> SearchResult:
        return SearchResult(
            query=SearchQuery(terms=["Engineer"]),
            jobs=[
                ScoredJob(
                    job=Job(
                        title="Chief Engineer",
                        company="Svitzer",
                        url="https://example.com/j/1",
                        source="remoteok",
                    ),
                    fit=FitScore(value=0.0, raw=0, matched={}),
                    relevance=Relevance(keep=True, reason="title matches 'engineer'"),
                    eligibility=Eligibility(
                        status=EligibilityStatus.REMOTE_UNVERIFIED,
                        reason="remote, but eligibility could not be confirmed",
                    ),
                )
            ],
            coverage=[SourceCoverage(source="remoteok", scanned=100, kept=1)],
        )

    def test_html_omits_the_breakdown_rather_than_printing_an_empty_one(self) -> None:
        html = HtmlReporter().render(self._zero_fit())
        assert "Chief Engineer" in html
        assert "fit 0%" in html
        assert 'class="matched"' not in html  # no empty breakdown paragraph

    def test_csv_writes_an_empty_matched_cell(self) -> None:
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(self._zero_fit()))))
        assert rows[0]["title"] == "Chief Engineer"
        assert rows[0]["matched"] == ""

    def test_json_keeps_the_empty_map_rather_than_dropping_the_field(self) -> None:
        payload = json.loads(JsonReporter().render(self._zero_fit()))
        assert payload["jobs"][0]["fit"]["matched"] == {}


class TestReporterFactory:
    def test_resolves_each_format_name(self) -> None:
        assert isinstance(reporter_for("json"), JsonReporter)
        assert isinstance(reporter_for("csv"), CsvReporter)
        assert isinstance(reporter_for("html"), HtmlReporter)

    def test_an_unknown_format_is_a_clear_error(self) -> None:
        try:
            reporter_for("pdf")
        except ValueError as exc:
            assert "pdf" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("expected ValueError for an unknown format")

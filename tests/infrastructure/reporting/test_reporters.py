"""Covers the reporters in `job_seeker.infrastructure.reporting`.

One test module for the three sibling reporters: they share the fixture and the same contract
(render a SearchResult to a string; never filter or reorder).
"""

from __future__ import annotations

import csv
import io
import json

from job_seeker.domain.models import (
    Eligibility,
    EligibilityStatus,
    FitScore,
    Job,
    Relevance,
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
        assert data["is_complete"] is False

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
        rows = list(csv.DictReader(io.StringIO(CsvReporter().render(result))))
        assert rows[0]["salary"] == "USD 150,000 - 180,000"

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

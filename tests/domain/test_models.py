"""Covers `job_seeker.domain.models`."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from job_seeker.domain.models import (
    ELIGIBLE_STATUSES,
    Eligibility,
    EligibilityStatus,
    FitScore,
    Job,
    ScoredJob,
)


class TestEligibilityStatusRendering:
    """A reporter interpolates a status straight into its output.

    A plain `(str, Enum)` keeps `Enum.__str__`, so `f"{status}"` renders
    "EligibilityStatus.HOME_BASED" rather than the wire value. JSON hides it, because a str
    subclass serializes by value, so only the human-facing formats break. Pin every path a
    reporter can take.
    """

    @pytest.mark.parametrize("status", list(EligibilityStatus))
    def test_str_renders_wire_value(self, status: EligibilityStatus) -> None:
        assert str(status) == status.value

    @pytest.mark.parametrize("status", list(EligibilityStatus))
    def test_fstring_renders_wire_value(self, status: EligibilityStatus) -> None:
        assert f"{status}" == status.value

    def test_percent_format_renders_wire_value(self) -> None:
        # noqa UP031: the %-format is the subject under test, not a style choice. A reporter
        # or log line may use it, and it routes through __str__, so it must render the value.
        assert "%s" % EligibilityStatus.HOME_BASED == "home-based"  # noqa: UP031

    def test_json_renders_wire_value(self) -> None:
        assert json.dumps({"status": EligibilityStatus.GLOBAL}) == '{"status": "global"}'

    def test_still_compares_equal_to_its_string(self) -> None:
        assert EligibilityStatus.GLOBAL == "global"


class TestEligibleStatuses:
    def test_holdable_statuses_are_eligible(self) -> None:
        for status in (
            EligibilityStatus.HOME_BASED,
            EligibilityStatus.REGIONAL,
            EligibilityStatus.GLOBAL,
            EligibilityStatus.REMOTE_UNVERIFIED,
        ):
            assert Eligibility(status=status).is_eligible

    def test_excluded_statuses_are_not_eligible(self) -> None:
        for status in (
            EligibilityStatus.EXCLUDED_LOCATION,
            EligibilityStatus.EXCLUDED_TIMEZONE,
            EligibilityStatus.EXCLUDED_AUTHORIZATION,
        ):
            assert not Eligibility(status=status).is_eligible

    def test_every_status_is_deliberately_classified(self) -> None:
        """Adding a status must be a decision, not an accidental exclusion."""
        assert set(EligibilityStatus) - ELIGIBLE_STATUSES == {
            EligibilityStatus.EXCLUDED_LOCATION,
            EligibilityStatus.EXCLUDED_TIMEZONE,
            EligibilityStatus.EXCLUDED_AUTHORIZATION,
        }


class TestJobFingerprint:
    def test_is_stable_across_instances(self, make_job: Callable[..., Job]) -> None:
        assert make_job().fingerprint == make_job().fingerprint

    def test_ignores_url_case_and_surrounding_whitespace(
        self, make_job: Callable[..., Job]
    ) -> None:
        a = make_job(url="https://example.com/jobs/1")
        b = make_job(url="  HTTPS://EXAMPLE.COM/JOBS/1 ")
        assert a.fingerprint == b.fingerprint

    def test_same_url_from_different_sources_collapses(self, make_job: Callable[..., Job]) -> None:
        assert make_job(source="himalayas").fingerprint == make_job(source="remotive").fingerprint

    def test_falls_back_to_title_and_company_when_url_is_empty(
        self, make_job: Callable[..., Job]
    ) -> None:
        assert make_job(url="").fingerprint == make_job(url="", source="other").fingerprint

    def test_the_fallback_still_tells_postings_apart(self, make_job: Callable[..., Job]) -> None:
        """Without this, a fallback returning a constant would pass the test above."""
        assert make_job(url="", title="A").fingerprint != make_job(url="", title="B").fingerprint
        assert (
            make_job(url="", company="A").fingerprint != make_job(url="", company="B").fingerprint
        )

    def test_different_roles_at_one_company_do_not_collide(
        self, make_job: Callable[..., Job]
    ) -> None:
        a = make_job(title="AI Engineer", url="https://example.com/jobs/1")
        b = make_job(title="Data Engineer", url="https://example.com/jobs/2")
        assert a.fingerprint != b.fingerprint

    def test_one_role_at_different_companies_does_not_collide(
        self, make_job: Callable[..., Job]
    ) -> None:
        a = make_job(company="Acme", url="https://example.com/jobs/1")
        b = make_job(company="Globex", url="https://example.com/jobs/2")
        assert a.fingerprint != b.fingerprint

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Fingerprint keys on URL first, so one posting aggregated from several boards yields "
            "a different fingerprint per board and survives dedup as duplicates. Cross-board "
            "dedup needs a normalized company+title key with URL only as a tiebreak."
        ),
    )
    def test_same_posting_from_different_boards_collapses(
        self, make_job: Callable[..., Job]
    ) -> None:
        boards = [
            make_job(source="himalayas", url="https://himalayas.app/jobs/acme-ai-engineer"),
            make_job(source="remotive", url="https://remotive.com/remote-jobs/12345"),
            make_job(source="remoteok", url="https://remoteok.com/l/98765"),
        ]
        assert len({job.fingerprint for job in boards}) == 1


class TestJobSearchText:
    def test_is_lower_cased(self, make_job: Callable[..., Job]) -> None:
        assert make_job(title="AI Engineer").search_text.startswith("ai engineer")

    def test_includes_title_description_and_location(self, make_job: Callable[..., Job]) -> None:
        text = make_job(
            title="AI Engineer", description="Build RAG systems", location="Worldwide"
        ).search_text
        assert "ai engineer" in text
        assert "build rag systems" in text
        assert "worldwide" in text


class TestScoredJob:
    def test_is_suitable_when_eligible(self, make_job: Callable[..., Job]) -> None:
        scored = ScoredJob(
            job=make_job(),
            fit=FitScore(value=10, matched=["python"]),
            eligibility=Eligibility(status=EligibilityStatus.GLOBAL),
        )
        assert scored.is_suitable

    def test_is_not_suitable_when_excluded_however_good_the_fit(
        self, make_job: Callable[..., Job]
    ) -> None:
        scored = ScoredJob(
            job=make_job(),
            fit=FitScore(value=99, matched=["python", "rag"]),
            eligibility=Eligibility(status=EligibilityStatus.EXCLUDED_AUTHORIZATION),
        )
        assert not scored.is_suitable

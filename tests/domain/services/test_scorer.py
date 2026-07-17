"""Covers `job_seeker.domain.services.scorer`."""

from __future__ import annotations

from collections.abc import Callable

from job_seeker.domain.models import Job
from job_seeker.domain.profile import Profile
from job_seeker.domain.services.scorer import ProfileScorer


class TestScoring:
    def test_sums_the_weights_of_matched_signals(self, make_job: Callable[..., Job]) -> None:
        profile = Profile(skills={r"\bpython\b": 3, r"\brag\b": 2, "kubernetes": 1})
        job = make_job(title="Python Engineer", description="Build RAG systems")
        score = ProfileScorer(profile).score(job)
        assert score.value == 5  # python (3) + rag (2), kubernetes absent
        assert set(score.matched) == {r"\bpython\b", r"\brag\b"}

    def test_matching_is_case_insensitive(self, make_job: Callable[..., Job]) -> None:
        """Real profiles weight "RAG", "FastAPI", "Neo4j". search_text is lower-cased, so a
        case-sensitive match would score every real profile at zero."""
        profile = Profile(skills={r"\bRAG\b": 2, "FastAPI": 3})
        job = make_job(title="Engineer", description="we use rag and fastapi")
        assert ProfileScorer(profile).score(job).value == 5

    def test_a_missing_signal_contributes_nothing(self, make_job: Callable[..., Job]) -> None:
        profile = Profile(skills={"golang": 3})
        assert ProfileScorer(profile).score(make_job(title="Python Engineer")).value == 0

    def test_a_signal_counts_once_regardless_of_repeats(self, make_job: Callable[..., Job]) -> None:
        profile = Profile(skills={r"\bpython\b": 3})
        job = make_job(title="Python Python", description="python python python")
        assert ProfileScorer(profile).score(job).value == 3

    def test_an_empty_skill_set_scores_zero(self, make_job: Callable[..., Job]) -> None:
        assert ProfileScorer(Profile()).score(make_job()).value == 0

    def test_scores_against_company_absent_text_only(self, make_job: Callable[..., Job]) -> None:
        """search_text is title + description + location; the company is not in it, so a skill
        that only appears in the company name does not score. Pins current behaviour."""
        profile = Profile(skills={"acme": 5})
        job = make_job(company="Acme", title="Engineer", description="", location="")
        assert ProfileScorer(profile).score(job).value == 0

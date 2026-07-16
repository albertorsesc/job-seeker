"""Shared fixtures.

`tests/` mirrors `src/job_seeker/`: one test module per source module, in the matching
directory. Fixtures used by more than one package live here; package-local fixtures belong
in that package's own conftest.

Tests never touch the network. Source adapters are exercised against mocked HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from job_seeker.domain.models import Job
from job_seeker.domain.profile import EligibilityRules, LocationProfile, Profile


@pytest.fixture
def make_job() -> Callable[..., Job]:
    """Build a Job, overriding only the fields a test actually cares about."""

    def _make(**overrides: Any) -> Job:
        defaults: dict[str, Any] = {
            "title": "AI Engineer",
            "company": "Acme",
            "url": "https://example.com/jobs/1",
            "source": "example",
        }
        return Job(**{**defaults, **overrides})

    return _make


@pytest.fixture
def profile() -> Profile:
    """A fictional seeker. Deliberately not anyone's real profile.

    Nothing candidate-specific belongs in this repo, including in fixtures: a test that
    encodes one person's skills would quietly re-hardcode what the profile exists to make
    configurable.
    """
    return Profile(
        name="Test Seeker",
        headline="Backend Engineer",
        seniority="senior",
        location=LocationProfile(country="Testland", timezone_utc_offset=-6.0),
        eligibility=EligibilityRules(
            eligible_regions=["testland", "worldwide", "anywhere"],
            disqualifying_authorization_terms=["us citizen", "security clearance"],
            location_lock_terms=["us only", "eu only"],
            max_timezone_distance_hours=1.0,
        ),
        # Mixed case on purpose. `Job.search_text` is lower-cased, so a scorer that compiles
        # these without re.IGNORECASE scores every real profile at zero: people write "RAG",
        # "FastAPI", "Neo4j", not "rag". An all-lower-case fixture would hide that.
        skills={r"\bPython\b": 3, r"\bRAG\b": 2, "kubernetes": 1},
        role_include=["engineer", "developer"],
        role_exclude=["marketing", "sales"],
        false_positive_terms=["support agent"],
        search_terms=["Backend Engineer"],
    )

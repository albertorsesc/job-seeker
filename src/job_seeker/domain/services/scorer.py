"""Score a posting by how well it matches the seeker's weighted skill signals.

The profile is a constructor dependency, not a per-call argument: the skills are regexes that
compile once here, and a scorer is fixed for the run. That also gives an invalid pattern a place
to fail loudly, at setup, before any posting is scored.

A domain service, pure reasoning over a Job. Matching is case-insensitive because `search_text`
is lower-cased while real profiles weight "RAG", "FastAPI", "Neo4j"; a case-sensitive match would
score every real profile at zero.
"""

from __future__ import annotations

import re

from job_seeker.domain.models import FitScore, Job
from job_seeker.domain.profile import Profile

# Skills are user-authored regexes run against board text, and `re` has no timeout, so a pattern
# with catastrophic backtracking against a long description could hang the run. Bounding the text
# caps the blast radius; a real description's signal is in the first few thousand characters.
_MAX_TEXT = 20_000


class ProfileScorer:
    """Compiles a profile's skill patterns once, then scores each posting against them."""

    def __init__(self, profile: Profile) -> None:
        self._signals = _compile(profile.skills)

    def score(self, job: Job) -> FitScore:
        """Sum the weights of every signal whose pattern appears in the posting text.

        A signal counts once no matter how often it appears: presence is what matters, not
        frequency, so a posting that says "python" ten times is not ten times a better fit.
        """
        text = job.search_text[:_MAX_TEXT]
        matched = [(pattern, weight) for pattern, weight in self._signals if pattern.search(text)]
        return FitScore(
            value=sum(weight for _, weight in matched),
            matched=[pattern.pattern for pattern, _ in matched],
        )


def _compile(skills: dict[str, int]) -> list[tuple[re.Pattern[str], int]]:
    compiled: list[tuple[re.Pattern[str], int]] = []
    for pattern, weight in skills.items():
        try:
            compiled.append((re.compile(pattern, re.IGNORECASE), weight))
        except re.error as exc:
            raise ValueError(f"profile skill {pattern!r} is not a valid regex ({exc}).") from exc
    return compiled

"""Keep the postings the seeker actually searched for, drop the noise.

Distinct from eligibility: an Aluminum Sourcing Director may be perfectly holdable (remote,
worldwide) and still not remotely what someone searching "AI Engineer" wants. Without this stage a
run returns every eligible posting, which is the noise every other job board already gives you.

A domain service, pure reasoning over a Job. It narrows by two profile-driven signals:

- **wanted**: the query's search terms plus the profile's `role_include`. A job is wanted when its
  title contains one of their words. Matching is word-level, so "ai" does not fire on "captain",
  and it is deliberately loose at the word level (an "AI Engineer" search keeps all engineer roles)
  because the scorer, not this filter, decides how good a match is; this only removes what is
  clearly off-topic. If neither signal is set there is nothing to narrow by, so everything passes.
- **excluded**: the profile's `role_exclude` (whole words in the title) and `false_positive_terms`
  (phrases anywhere in the text, for a matched keyword that names a human role rather than the
  thing the seeker builds). An exclusion always wins over a wanted match.
"""

from __future__ import annotations

import re

from job_seeker.domain.models import Job, Relevance
from job_seeker.domain.profile import Profile

_WORD_SPLIT = re.compile(r"[^\w]+")
_WORD_CACHE: dict[str, re.Pattern[str]] = {}


class RelevanceFilter:
    """Decides which postings match what the seeker is looking for, and records why."""

    def __init__(self, profile: Profile) -> None:
        self._role_include_words = _words(profile.role_include)
        # Sorted once here: `_first_hit` reports the first matching word, and a stable order keeps
        # the reason deterministic without re-sorting on every job.
        self._role_exclude_words = sorted(_words(profile.role_exclude))
        self._false_positive_terms = [t.lower() for t in profile.false_positive_terms]

    def assess(self, job: Job, terms: list[str]) -> Relevance:
        """Judge one posting against the seeker's terms, returning the verdict and its reason."""
        return self._assess(job, self._wanted(terms))

    def assess_all(self, jobs: list[Job], terms: list[str]) -> list[tuple[Job, Relevance]]:
        """Judge a batch, computing the wanted set once, pairing each job with its verdict.

        Every job is returned, kept or dropped, so a caller can act on the verdict (keep the
        on-topic ones) without losing the reason the others were dropped.
        """
        wanted = self._wanted(terms)
        return [(job, self._assess(job, wanted)) for job in jobs]

    def _wanted(self, terms: list[str]) -> list[str]:
        """The words that mark a job on-topic, sorted once so a matched reason is deterministic."""
        return sorted(self._role_include_words | _words(terms))

    def _assess(self, job: Job, wanted: list[str]) -> Relevance:
        title = job.title.lower()
        excluded = _first_hit(self._role_exclude_words, title)
        if excluded is not None:
            return Relevance(keep=False, reason=f"excluded role term '{excluded}'")
        if self._false_positive_terms:
            text = job.search_text  # a rebuilt property; read it once
            false_positive = next((t for t in self._false_positive_terms if t in text), None)
            if false_positive is not None:
                return Relevance(keep=False, reason=f"names a human role ('{false_positive}')")
        if not wanted:  # no way to narrow: the seeker gets everything eligible
            return Relevance(keep=True, reason="no search terms set")
        matched = _first_hit(wanted, title)
        if matched is not None:
            return Relevance(keep=True, reason=f"title matches '{matched}'")
        return Relevance(keep=False, reason="title matches no search term")


def _words(phrases: list[str]) -> set[str]:
    """The distinct lower-cased words across a list of terms or role phrases."""
    return {word for phrase in phrases for word in _WORD_SPLIT.split(phrase.lower()) if word}


def _first_hit(words: list[str], text: str) -> str | None:
    """The first matching word whose whole-word form appears in text, or None.

    Returns the word, not just a bool, so the caller can name it in the reason: "title matches
    'engineer'" is worth more to a puzzled seeker than "kept". `words` is pre-sorted by the caller,
    so the reported word is deterministic; a set's own order would make the same job report a
    different reason from one run to the next. The keep/drop verdict never depends on which is first.
    """
    return next((word for word in words if _word_in(word, text)), None)


def _word_in(word: str, text: str) -> bool:
    pattern = _WORD_CACHE.get(word)
    if pattern is None:
        pattern = _WORD_CACHE[word] = re.compile(rf"\b{re.escape(word)}\b")
    return pattern.search(text) is not None

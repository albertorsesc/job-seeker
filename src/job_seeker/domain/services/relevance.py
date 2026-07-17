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

from job_seeker.domain.models import Job
from job_seeker.domain.profile import Profile

_WORD_SPLIT = re.compile(r"[^\w]+")
_WORD_CACHE: dict[str, re.Pattern[str]] = {}


class RelevanceFilter:
    """Decides which postings match what the seeker is looking for."""

    def __init__(self, profile: Profile) -> None:
        self._role_include_words = _words(profile.role_include)
        self._role_exclude_words = _words(profile.role_exclude)
        self._false_positive_terms = [t.lower() for t in profile.false_positive_terms]

    def is_relevant(self, job: Job, terms: list[str]) -> bool:
        return self._matches(job, self._role_include_words | _words(terms))

    def filter(self, jobs: list[Job], terms: list[str]) -> list[Job]:
        # Compute the wanted set once for the whole batch, not once per job.
        wanted = self._role_include_words | _words(terms)
        return [job for job in jobs if self._matches(job, wanted)]

    def _matches(self, job: Job, wanted: set[str]) -> bool:
        title = job.title.lower()
        if any(_word_in(word, title) for word in self._role_exclude_words):
            return False
        if self._false_positive_terms:
            text = job.search_text  # a rebuilt property; read it once
            if any(term in text for term in self._false_positive_terms):
                return False
        if not wanted:  # no way to narrow: the seeker gets everything eligible
            return True
        return any(_word_in(word, title) for word in wanted)


def _words(phrases: list[str]) -> set[str]:
    """The distinct lower-cased words across a list of terms or role phrases."""
    return {word for phrase in phrases for word in _WORD_SPLIT.split(phrase.lower()) if word}


def _word_in(word: str, text: str) -> bool:
    pattern = _WORD_CACHE.get(word)
    if pattern is None:
        pattern = _WORD_CACHE[word] = re.compile(rf"\b{re.escape(word)}\b")
    return pattern.search(text) is not None

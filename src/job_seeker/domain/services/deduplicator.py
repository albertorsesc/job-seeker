"""Collapse the same posting seen on more than one board into a single record.

Identity is where dedup was broken before: `Job.fingerprint` keyed on URL, but the same role
carries a different apply URL on every board, so the same posting never merged, which is the
entire point of aggregating boards. Identity here is the normalized company plus the normalized
title, so the URL differences do not matter and "Acme" / "Acme, Inc." / "ACME LLC" count as one
company.

This is a domain service, not a port: it is pure reasoning over Jobs and touches nothing external,
so it is deterministic and needs no injection. The orchestrator calls it directly.

A deliberate v1 tradeoff: normalizing the title conservatively (case, punctuation, whitespace) can
under-merge ("Senior AI Engineer" vs "AI Engineer" stay separate) but will not over-merge. Silent
over-dedup, dropping a genuinely different role, is the worse failure for a job seeker, so the key
errs toward keeping too much rather than too little.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from job_seeker.domain.models import EligibilityHints, Job

# Company-name suffixes that carry no identity, stripped so legal-form noise does not split a
# match. Order-independent; matched as whole trailing tokens after punctuation is removed.
_LEGAL_SUFFIXES = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "gmbh",
        "bv",
        "ag",
        "plc",
        "sa",
        "srl",
        "pty",
        "llp",
        "lp",
    }
)
_PUNCTUATION = re.compile(r"[^\w\s]")
_WHITESPACE = re.compile(r"\s+")
# Older than any real posting, so a record with no date sorts below every dated one.
_UNDATED = datetime.min.replace(tzinfo=UTC)


# The vocabulary of the identity key. Bumping this is not a refactor: every posting a seeker has
# dismissed is filed under a key computed by the old rules, so a changed key orphans their
# decisions and the postings they banned come back. Change it deliberately, and read
# `test_deduplicator`'s golden table first, which exists to make an accidental change fail loudly.
IDENTITY_VERSION = 1


def posting_identity(job: Job, /) -> str:
    """What makes two postings the same posting: normalized company and title.

    URL and source are ignored, which is the point. Every board has its own apply URL for the same
    role, so a URL key never merges anything, and that was the bug this module was written to fix.

    One function, not two, because the same question is asked twice: "the same posting on two
    boards" during a run, and "the same posting on two Tuesdays" across runs. Two normalizers that
    have to agree will drift, and the drift surfaces months later as a dismissal that quietly
    stopped working.

    When the company normalizes to empty (blank, or a name that is only a legal form), the title
    alone is too weak a key: every same-title posting would collapse into one and distinct roles
    would be silently dropped. So a company-less job keys on its URL instead, which never merges it
    with another posting.
    """
    company = _normalize_company(job.company)
    title = _normalize_title(job.title)
    if not company:
        return f"url:{job.url.strip().lower()}|{title}"
    return f"{company}|{title}"


class Deduplicator:
    """Merges postings that are the same job across boards, keeping the best representative."""

    def identity(self, job: Job) -> str:
        """The cross-board key. See `posting_identity`, which this delegates to."""
        return posting_identity(job)

    def dedupe(self, jobs: list[Job]) -> list[Job]:
        """One record per identity, preserving the order each identity was first seen.

        The representative is the freshest posting, then the richest: a re-post is the more current
        description of the role, and richness breaks a tie so an equally recent pair keeps the
        fuller record.

        **The others are not discarded, they complete it.** Choosing a representative and throwing
        the rest away lost real data: a copy posted an hour later with no salary beat one carrying
        150,000, and the pay vanished from the answer. Boards publish different subsets of the same
        posting, which is the whole reason to aggregate them, so a field the representative lacks
        is filled from a sibling and a field it has is never overruled.

        The representative's own identity is untouched: `url` and `source` keep pointing at the
        posting a seeker would actually open. So a merged record can carry a salary its own board
        did not publish, which is the point, and is why `source` names the representative rather
        than claiming provenance for every field.
        """
        groups: dict[str, list[Job]] = {}
        order: list[str] = []
        for job in jobs:
            key = self.identity(job)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(job)
        return [_merged(groups[key]) for key in order]


def _merged(group: list[Job]) -> Job:
    """The group's representative, completed from its siblings. A single job is returned as-is."""
    if len(group) == 1:
        return group[0]
    winner, *rest = sorted(group, key=_rank, reverse=True)
    filled = {
        field: value
        for field in _COMPLETABLE
        if not _is_present(getattr(winner, field))
        for value in [_first_present(rest, field)]
        if value is not None
    }
    hints = _merged_hints(winner, rest)
    if hints is not None:
        filled["hints"] = hints
    return winner.model_copy(update=filled) if filled else winner


# Fields a sibling may complete. Deliberately excludes `title`, `company`, `url` and `source`:
# the first two are the identity every member of the group shares, and the last two must keep
# describing the posting the representative actually is.
_COMPLETABLE = ("description", "location", "salary", "seniority", "employment_type", "posted_at")


def _is_present(value: object) -> bool:
    """Whether a field carries a claim. An empty string and None are both "nothing said"."""
    if value is None:
        return False
    return value.strip() != "" if isinstance(value, str) else True


def _first_present(jobs: list[Job], field: str) -> object | None:
    return next((v for job in jobs for v in [getattr(job, field)] if _is_present(v)), None)


def _merged_hints(winner: Job, rest: list[Job]) -> EligibilityHints | None:
    """The winner's hints with each unknown half filled from a sibling, or None if nothing to add.

    Filled per half, not wholesale: the two restrictions are independent claims, and `None` means
    "this board said nothing" while `()` means "it said there is no restriction". A board that
    actually stated something is never overruled by one that did not, so this only ever turns an
    unknown into a known.
    """
    location = winner.hints.location_restrictions
    timezone = winner.hints.timezone_restrictions
    if location is None:
        location = next(
            (
                j.hints.location_restrictions
                for j in rest
                if j.hints.location_restrictions is not None
            ),
            None,
        )
    if timezone is None:
        timezone = next(
            (
                j.hints.timezone_restrictions
                for j in rest
                if j.hints.timezone_restrictions is not None
            ),
            None,
        )
    if (location, timezone) == (
        winner.hints.location_restrictions,
        winner.hints.timezone_restrictions,
    ):
        return None
    return EligibilityHints(location_restrictions=location, timezone_restrictions=timezone)


def _rank(job: Job) -> tuple[datetime, int]:
    """Sort key for choosing a group's survivor: newer wins, then more complete."""
    richness = sum(
        (
            bool(job.description.strip()),
            job.salary is not None,
            bool(job.seniority.strip()),
            job.hints.location_restrictions is not None,
        )
    )
    return (job.posted_at or _UNDATED, richness)


def _normalize_company(company: str) -> str:
    tokens = _tokens(company)
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _normalize_title(title: str) -> str:
    return " ".join(_tokens(title))


def _tokens(text: str) -> list[str]:
    """Casefold, drop punctuation, split on whitespace. The shared normalization primitive."""
    cleaned = _PUNCTUATION.sub(" ", text.casefold())
    return _WHITESPACE.sub(" ", cleaned).strip().split()

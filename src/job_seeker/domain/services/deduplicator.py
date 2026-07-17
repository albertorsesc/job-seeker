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

from job_seeker.domain.models import Job

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


class Deduplicator:
    """Merges postings that are the same job across boards, keeping the best representative."""

    def identity(self, job: Job) -> str:
        """The cross-board key: normalized company and title. URL and source are ignored."""
        return f"{_normalize_company(job.company)}|{_normalize_title(job.title)}"

    def dedupe(self, jobs: list[Job]) -> list[Job]:
        """One record per identity, preserving the order each identity was first seen.

        Within a group the survivor is the freshest posting, then the richest: freshness first so
        a stale duplicate cannot shadow a newer one and get filtered out later by age, richness as
        the tiebreak so the record kept is the most complete of an equally recent set.
        """
        best: dict[str, Job] = {}
        order: list[str] = []
        for job in jobs:
            key = self.identity(job)
            incumbent = best.get(key)
            if incumbent is None:
                best[key] = job
                order.append(key)
            elif _rank(job) > _rank(incumbent):
                best[key] = job
        return [best[key] for key in order]


def _rank(job: Job) -> tuple[datetime, int]:
    """Sort key for choosing a group's survivor: newer wins, then more complete."""
    richness = sum(
        (
            bool(job.description.strip()),
            bool(job.salary.strip()),
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

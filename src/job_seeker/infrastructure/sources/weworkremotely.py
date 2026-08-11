"""The WeWorkRemotely adapter, and the first board this engine reads over RSS.

Verified against the live feed on 2026-08-11:

- `GET https://weworkremotely.com/remote-jobs.rss` returns exactly 100 items, ten from each of the
  board's ten categories. `?page=2` returns the same hundred, so there is no pagination to walk:
  this is a window onto a much larger board, and the coverage it reports says so.
- Each item carries `title, region, country, state, skills, category, type, description, pubDate,
  expires_at, guid, link`. The title is "Company: Role".
- `country` is the eligibility field, and it names countries the ISO 3166 way ("United States of
  America"), comma-separated with an Oxford "and", each entry prefixed by its flag emoji.
- There is no pay field. Two thirds of the descriptions mention money somewhere in their prose,
  which is not the same thing as the board publishing a figure, so postings from here carry no
  salary at all rather than one parsed out of marketing copy.

**`region` is not a restriction, and this is the fact that shapes the adapter.** It reads like one:
93 of the 100 items say "Anywhere in the World". But 14 of those also name a `country` that
restricts them, one of them to the United States alone. It is a category the employer picks when
posting, not a claim about who may hold the role, so reporting it as a structured restriction would
tell the classifier a US-only job is open worldwide, which is the exact failure the eligibility
layer exists to prevent. `country` is reported as the restriction; `region` goes to `Job.location`,
where it is one input to the text path rather than the whole verdict.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
from bs4 import Tag

from job_seeker.domain.models import (
    EligibilityHints,
    Job,
    SearchQuery,
    SourceResult,
)
from job_seeker.infrastructure.sources import base

_FEED_URL = "https://weworkremotely.com/remote-jobs.rss"

# Each country in the `country` field is prefixed by its flag, a pair of regional-indicator
# symbols. That prefix is the only delimiter that works: the list separator is a comma with an
# Oxford "and", and "Bosnia and Herzegovina" contains the same word the separator does.
_FLAG = re.compile(r"[\U0001f1e6-\U0001f1ff]{2}")
# What is left dangling on a split part: ", ", " and ", or ", and ".
_LIST_TAIL = re.compile(r"[\s,]*(?:\band\b)?[\s,]*$")


class WeWorkRemotelySource:
    """Fetches the WeWorkRemotely RSS feed and normalizes it into canonical `Job`s."""

    name = "weworkremotely"

    def is_available(self) -> bool:
        # No credential, no optional dependency, and no I/O.
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        """Fetch the feed, normalize, and report. Never raises: a failure is a SourceResult.error."""
        cutoff = base.age_cutoff(query.max_age_days)
        now = datetime.now(UTC)
        try:
            with base.build_client() as client:
                document = base.get_xml(client, _FEED_URL, root="rss")
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")

        jobs: list[Job] = []
        scanned = 0
        for item in document.find_all("item"):
            if not isinstance(item, Tag):
                continue
            scanned += 1
            job = _normalize(item)
            if job is None or base.is_stale(job.posted_at, cutoff) or _has_expired(item, now):
                continue
            jobs.append(job)
            if len(jobs) >= query.scan_depth_per_source:
                break

        # Always truncated. The feed is ten postings per category from a board that lists far more,
        # and the page parameter is ignored, so even a run that read every item saw a window.
        return SourceResult(source=self.name, jobs=jobs, scanned=scanned, truncated=True)


def _normalize(item: Tag) -> Job | None:
    """One feed item into a canonical Job, or None if it is unusable.

    Every read is defended: `fetch` runs in a thread-pool worker and must not raise on a feed that
    changed shape overnight.
    """
    company, title = _company_and_role(base.element_text(item, "title"))
    url = base.element_text(item, "link") or base.element_text(item, "guid")
    if not title or not url:
        return None

    countries = _countries(base.element_text(item, "country"))
    return Job(
        title=title,
        company=company,
        url=url,
        source=WeWorkRemotelySource.name,
        description=base.clean_html(base.element_text(item, "description")),
        location=", ".join(countries) if countries else base.element_text(item, "region"),
        posted_at=base.to_utc_from_email_date(base.element_text(item, "pubDate")),
        employment_type=base.element_text(item, "type"),
        hints=EligibilityHints(location_restrictions=countries or None),
    )


def _company_and_role(title: str) -> tuple[str, str]:
    """The board writes one field as "Company: Role". Split on the first colon only, so a role
    that punctuates itself ("Engineer: Platform") keeps its own text."""
    company, separator, role = title.partition(": ")
    return (company.strip(), role.strip()) if separator else ("", title.strip())


def _countries(raw: str) -> tuple[str, ...]:
    """The `country` field as the places it names, as the board spelled them.

    Reported verbatim, ISO long names and all. Deciding whether "United States of America" is
    somewhere the seeker may work is the classifier's job, and an adapter that rewrote the name
    first would be answering that question with a board's vocabulary instead of a profile's.
    """
    parts = (_LIST_TAIL.sub("", part).strip() for part in _FLAG.split(raw))
    named = tuple(part for part in parts if part)
    if named or not raw.strip():
        return named
    # The board wrote something this could not read: flags with no names, a truncated field, a
    # name that failed to serialize. Reporting () would become `None` at the call site and mean
    # "the board said nothing", and the text path would then read the "Anywhere in the World"
    # region and promote a restricted posting to global. The board's own text excludes instead,
    # which is the safe direction and shows the seeker exactly what it said.
    return (raw.strip(),)


def _has_expired(item: Tag, now: datetime) -> bool:
    """Whether the board's own expiry has passed. An item with no readable expiry has not expired.

    The board publishes when each posting closes, roughly 30 days out. A seeker cannot apply to a
    closed one, and the age filter only covers this while `max_age_days` happens to be shorter than
    the board's window.
    """
    expires = base.to_utc_from_email_date(base.element_text(item, "expires_at"))
    return expires is not None and expires < now

"""Covers `job_seeker.infrastructure.sources.weworkremotely`.

Every `country` string here is a real value captured from the live feed on 2026-08-11, including
the two-country "and" form and the Oxford-comma form, because the reason to trust the parser is
that it reads what the board actually emits.

No network: respx mocks the feed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import respx

from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources.weworkremotely import WeWorkRemotelySource, _countries

FEED = "https://weworkremotely.com/remote-jobs.rss"
US_FLAG = "\U0001f1fa\U0001f1f8"


def _rfc2822(when: datetime) -> str:
    return format_datetime(when)


def _item(**overrides: Any) -> str:
    fields: dict[str, str] = {
        "title": "Hospitable: Customer Advocate Lead",
        "region": "Anywhere in the World",
        "country": "",
        "state": "",
        "skills": "Technical Support, Leadership",
        "category": "Customer Support",
        "type": "Full-Time",
        "description": "<p>Build <strong>support</strong> systems.</p>",
        "pubDate": _rfc2822(datetime.now(UTC) - timedelta(days=1)),
        "expires_at": _rfc2822(datetime.now(UTC) + timedelta(days=29)),
        "guid": "https://weworkremotely.com/remote-jobs/hospitable-lead",
        "link": "https://weworkremotely.com/remote-jobs/hospitable-lead",
        **overrides,
    }
    body = "".join(f"<{name}><![CDATA[{value}]]></{name}>" for name, value in fields.items())
    return f"<item>{body}</item>"


def _feed(*items: str) -> str:
    return f"<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'><channel>{''.join(items)}</channel></rss>"


def _fetch(*items: str, **query: Any) -> SourceResult:
    with respx.mock:
        respx.get(FEED).mock(return_value=httpx.Response(200, text=_feed(*items)))
        return WeWorkRemotelySource().fetch(SearchQuery(max_age_days=None, **query))


class TestNormalization:
    def test_it_reads_a_posting_end_to_end(self) -> None:
        job = _fetch(_item()).jobs[0]
        assert job.title == "Customer Advocate Lead"
        assert job.company == "Hospitable"
        assert job.url == "https://weworkremotely.com/remote-jobs/hospitable-lead"
        assert job.source == "weworkremotely"
        assert job.employment_type == "Full-Time"

    def test_the_description_arrives_as_text_not_markup(self) -> None:
        assert _fetch(_item()).jobs[0].description == "Build support systems."

    def test_the_title_splits_on_the_first_colon_only(self) -> None:
        """ "Company: Role" is one field, and a role can punctuate itself."""
        job = _fetch(_item(title="Acme: Engineer: Platform")).jobs[0]
        assert (job.company, job.title) == ("Acme", "Engineer: Platform")

    def test_a_title_with_no_company_prefix_keeps_the_whole_string_as_the_role(self) -> None:
        job = _fetch(_item(title="Senior AI Engineer")).jobs[0]
        assert (job.company, job.title) == ("", "Senior AI Engineer")

    def test_it_falls_back_to_the_guid_when_the_link_is_missing(self) -> None:
        assert _fetch(_item(link="")).jobs[0].url.endswith("hospitable-lead")

    def test_an_item_with_no_usable_url_is_dropped_rather_than_faked(self) -> None:
        assert _fetch(_item(link="", guid="")).jobs == []

    def test_the_publication_date_is_read_as_an_aware_datetime(self) -> None:
        """RSS states RFC 2822 dates. A naive one compared against `now` raises during age
        filtering, single-threaded, taking the run with it."""
        posted = _fetch(_item(pubDate="Tue, 11 Aug 2026 16:03:20 +0000")).jobs[0].posted_at
        assert posted == datetime(2026, 8, 11, 16, 3, 20, tzinfo=UTC)

    def test_an_unreadable_date_leaves_the_posting_undated_rather_than_dropping_it(self) -> None:
        job = _fetch(_item(pubDate="whenever")).jobs[0]
        assert job.posted_at is None


class TestCountriesAreTheRestriction:
    """The eligibility field, and the reason this board is worth having."""

    def test_a_two_country_list_splits_on_the_flags_not_the_word_and(self) -> None:
        assert _countries("🇨🇦 Canada and 🇺🇸 United States of America") == (
            "Canada",
            "United States of America",
        )

    def test_an_oxford_comma_list_splits_cleanly(self) -> None:
        assert _countries(
            "🇦🇺 Australia, 🇬🇧 United Kingdom of Great Britain and Northern Ireland, "
            "and 🇺🇸 United States of America"
        ) == (
            "Australia",
            "United Kingdom of Great Britain and Northern Ireland",
            "United States of America",
        )

    def test_a_country_whose_own_name_contains_and_survives_intact(self) -> None:
        """The separator and the country name are the same word, which is why the flag is the
        delimiter and the comma is not."""
        assert _countries("🇧🇦 Bosnia and Herzegovina, and 🇭🇷 Croatia") == (
            "Bosnia and Herzegovina",
            "Croatia",
        )

    def test_the_countries_reach_the_job_as_stated_restrictions(self) -> None:
        job = _fetch(_item(country="🇺🇸 United States of America")).jobs[0]
        assert job.hints.location_restrictions == ("United States of America",)

    def test_the_names_are_reported_as_the_board_spelled_them(self) -> None:
        """Deciding that "United States of America" is the United States is the classifier's job.
        An adapter that rewrote it first would answer the eligibility question in the board's
        vocabulary rather than the profile's."""
        assert _fetch(_item(country="🇺🇸 United States of America")).jobs[
            0
        ].hints.location_restrictions == ("United States of America",)

    def test_a_country_field_it_cannot_read_excludes_rather_than_going_quiet(self) -> None:
        """A flag with no name behind it. Reporting nothing would mean "the board said nothing",
        the text path would then read the "Anywhere in the World" region, and a restricted posting
        would be promoted to global. The board's own text excludes instead."""
        job = _fetch(_item(country=US_FLAG)).jobs[0]
        assert job.hints.location_restrictions == (US_FLAG,)

    def test_an_empty_country_field_still_means_the_board_said_nothing(self) -> None:
        assert _fetch(_item(country="")).jobs[0].hints.location_restrictions is None


class TestRegionIsNotARestriction:
    """The measured trap. On the live feed, 93 of 100 items say "Anywhere in the World" and 14 of
    those name a country that restricts them, one to the United States alone. Reported as a
    structured restriction it would tell the classifier a US-only job is open worldwide."""

    def test_a_worldwide_region_never_becomes_a_stated_restriction(self) -> None:
        job = _fetch(_item(region="Anywhere in the World", country="")).jobs[0]
        assert job.hints.location_restrictions is None

    def test_a_worldwide_region_beside_a_us_country_reports_only_the_country(self) -> None:
        """The exact contradiction the live feed carries."""
        job = _fetch(
            _item(region="Anywhere in the World", country="🇺🇸 United States of America")
        ).jobs[0]
        assert job.hints.location_restrictions == ("United States of America",)

    def test_the_region_still_reaches_the_posting_as_location_text(self) -> None:
        """Not discarded: with no country it is the only place signal the board gives, and the
        text path weighs it against the posting's own words instead of taking it as a verdict."""
        assert _fetch(_item(region="Anywhere in the World")).jobs[0].location == (
            "Anywhere in the World"
        )

    def test_the_countries_win_the_location_text_when_the_board_names_them(self) -> None:
        job = _fetch(_item(region="Anywhere in the World", country="🇨🇦 Canada")).jobs[0]
        assert job.location == "Canada"


class TestNoPayIsReported:
    def test_a_posting_carries_no_salary(self) -> None:
        """The board publishes no pay field. Two thirds of its descriptions mention money in
        prose, and a figure read out of marketing copy is worse than no figure."""
        assert (
            _fetch(_item(description="<p>Salary: $200,000 OTE plus equity</p>")).jobs[0].salary
            is None
        )


class TestFiltering:
    def test_an_expired_posting_is_dropped(self) -> None:
        """The board says when each posting closes. A seeker cannot apply to a closed one, and the
        age filter only catches it while `max_age_days` is shorter than the board's own window."""
        expired = _item(expires_at=_rfc2822(datetime.now(UTC) - timedelta(days=1)))
        assert _fetch(expired).jobs == []

    def test_an_expired_posting_still_counts_as_scanned(self) -> None:
        """Coverage reports what was read, not what survived."""
        expired = _item(expires_at=_rfc2822(datetime.now(UTC) - timedelta(days=1)))
        assert _fetch(expired).scanned == 1

    def test_a_posting_with_no_readable_expiry_is_kept(self) -> None:
        assert len(_fetch(_item(expires_at="")).jobs) == 1

    def test_a_posting_older_than_the_age_window_is_dropped(self) -> None:
        old = _item(pubDate=_rfc2822(datetime.now(UTC) - timedelta(days=60)))
        with respx.mock:
            respx.get(FEED).mock(return_value=httpx.Response(200, text=_feed(old)))
            result = WeWorkRemotelySource().fetch(SearchQuery(max_age_days=30))
        assert result.jobs == []

    def test_scan_depth_bounds_what_is_kept(self) -> None:
        result = _fetch(*[_item(link=f"https://wwr.test/{n}") for n in range(5)])
        assert len(result.jobs) == 5
        assert (
            len(
                _fetch(
                    *[_item(link=f"https://wwr.test/{n}") for n in range(5)],
                    scan_depth_per_source=2,
                ).jobs
            )
            == 2
        )


class TestCoverage:
    def test_it_always_reports_a_truncated_scan(self) -> None:
        """Ten postings per category from a board that lists far more, and `?page=2` returns the
        same hundred. A run that read every item still saw a window."""
        assert _fetch(_item()).truncated is True

    def test_a_feed_that_is_down_is_reported_not_raised(self) -> None:
        with respx.mock:
            respx.get(FEED).mock(return_value=httpx.Response(503))
            result = WeWorkRemotelySource().fetch(SearchQuery())
        assert result.failed
        assert result.jobs == []

    def test_a_challenge_page_served_as_the_feed_is_reported_as_a_failure(self) -> None:
        """The silent-failure shape: an XML parser accepts an HTML page and finds no items, so the
        board would report a clean run that happened to find nothing."""
        with respx.mock:
            respx.get(FEED).mock(
                return_value=httpx.Response(200, text="<html><body>Just a moment...</body></html>")
            )
            result = WeWorkRemotelySource().fetch(SearchQuery())
        assert result.failed

    def test_an_empty_but_valid_feed_is_a_success_with_no_jobs(self) -> None:
        """Distinct from the challenge page: this is the board answering, with nothing to say."""
        with respx.mock:
            respx.get(FEED).mock(
                return_value=httpx.Response(200, text="<rss><channel></channel></rss>")
            )
            result = WeWorkRemotelySource().fetch(SearchQuery())
        assert not result.failed
        assert result.jobs == []

    def test_a_feed_that_stopped_being_rss_is_reported_rather_than_read_as_empty(self) -> None:
        """The same silent zero from the other direction: valid XML, wrong document. Every field
        this adapter reads is an RSS field, so an Atom feed would yield nothing and say nothing."""
        atom = "<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>x</title></entry></feed>"
        with respx.mock:
            respx.get(FEED).mock(return_value=httpx.Response(200, text=atom))
            result = WeWorkRemotelySource().fetch(SearchQuery())
        assert result.failed

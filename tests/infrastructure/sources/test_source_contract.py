"""Every built-in adapter must honor the JobSource contract, enforced automatically.

The contract is a Protocol docstring: `fetch` must not raise, `is_available` must not raise or
do I/O, and a result must carry the source's own name. Prose alone lets a careless second adapter
break it silently, and mypy only checks the method shapes. This parametrizes over every built-in
factory, so a new adapter is held to the contract the moment it is added to `_BUILTINS`, with no
new test to write.

Network is blocked, not mocked to succeed: running inside `respx.mock` with a catch-all failure
route means any adapter that reaches the network is exercised against a down board, which is
exactly when the never-raise contract must hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx

from job_seeker.application.ports import JobSource
from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources import defaults

BUILTINS = list(defaults._BUILTINS.items())


@pytest.mark.parametrize("name,factory", BUILTINS, ids=[n for n, _ in BUILTINS])
class TestEveryBuiltinHonorsTheContract:
    def test_the_factory_constructs_without_raising(self, name: str, factory: object) -> None:
        factory()  # type: ignore[operator]

    def test_name_matches_the_registration_key(self, name: str, factory: object) -> None:
        assert factory().name == name  # type: ignore[operator]

    def test_the_name_is_a_class_level_constant(self, name: str, factory: object) -> None:
        """Stricter than the port, and deliberately so.

        `JobSource.name` is a read-only property, which a plain attribute and a `@property` both
        satisfy; that breadth exists so a source whose name is instance state still conforms. An
        adapter that ships here is held to the narrower rule, because each adapter's `_normalize`
        is a module function that builds `Job.source` from `TheSource.name`, with no instance in
        hand. A property evaluates to the descriptor object there, and pydantic
        rejects it as a non-string, so the failure lands in normalization rather than here.

        Read through `type(instance)` rather than off the factory: a factory may be a lambda
        rather than the class itself, and the rule is about the class.
        """
        instance = factory()  # type: ignore[operator]
        assert type(instance).name == name

    def test_is_available_returns_a_bool_without_io_or_raising(
        self, name: str, factory: object
    ) -> None:
        # No routes registered: respx raises on any un-mocked request, so this fails loudly if an
        # adapter's is_available touches the network, enforcing the "no I/O" clause.
        with respx.mock:
            result = factory().is_available()  # type: ignore[operator]
        assert isinstance(result, bool)

    def test_fetch_reports_failure_instead_of_raising_when_the_board_is_down(
        self, name: str, factory: object
    ) -> None:
        with respx.mock:
            respx.route().mock(side_effect=httpx.ConnectError("board is down"))
            result = factory().fetch(SearchQuery(max_age_days=None))  # type: ignore[operator]
        assert result.failed, f"{name}.fetch must report a down board, not raise"
        assert result.source == name
        assert result.jobs == []


# One sample payload per board, in that board's own dialect, so the query-honesty contract below
# can drive every adapter through a real fetch. Each builder makes `count` postings, dated `age`
# days ago. Adding a board means adding a line here, which is the point: the contract is what a
# new adapter is held to, and it cannot be held to it without something to answer with.
def _himalayas(count: int, age: int) -> respx.Route:
    posted = int((datetime.now(UTC) - timedelta(days=age)).timestamp())
    jobs = [
        {
            "title": f"AI Engineer {n}",
            "companyName": f"Company {n}",
            "applicationLink": f"https://himalayas.test/{n}",
            "guid": str(n),
            "pubDate": posted,
            "description": "python",
        }
        for n in range(count)
    ]
    return respx.get(url__startswith="https://himalayas.app/jobs/api").mock(
        side_effect=[
            httpx.Response(200, json={"jobs": jobs}),
            httpx.Response(200, json={"jobs": []}),
        ]
    )


def _remoteok(count: int, age: int) -> respx.Route:
    posted = int((datetime.now(UTC) - timedelta(days=age)).timestamp())
    records: list[dict[str, object]] = [{"legal": "boilerplate"}]
    records += [
        {
            "id": str(n),
            "position": f"AI Engineer {n}",
            "company": f"Company {n}",
            "url": f"https://remoteok.test/{n}",
            "epoch": posted,
            "description": "python",
        }
        for n in range(count)
    ]
    return respx.get("https://remoteok.com/api").mock(
        return_value=httpx.Response(200, json=records)
    )


def _remotive(count: int, age: int) -> respx.Route:
    posted = (datetime.now(UTC) - timedelta(days=age)).strftime("%Y-%m-%dT%H:%M:%S")
    jobs = [
        {
            "title": f"AI Engineer {n}",
            "company_name": f"Company {n}",
            "url": f"https://remotive.test/{n}",
            "publication_date": posted,
            "description": "python",
        }
        for n in range(count)
    ]
    return respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json={"jobs": jobs})
    )


def _weworkremotely(count: int, age: int) -> respx.Route:
    posted = format_datetime(datetime.now(UTC) - timedelta(days=age))
    items = "".join(
        f"<item><title>Company {n}: AI Engineer {n}</title>"
        f"<link>https://wwr.test/{n}</link><pubDate>{posted}</pubDate>"
        f"<description>python</description></item>"
        for n in range(count)
    )
    feed = f"<?xml version='1.0'?><rss version='2.0'><channel>{items}</channel></rss>"
    return respx.get("https://weworkremotely.com/remote-jobs.rss").mock(
        return_value=httpx.Response(200, text=feed)
    )


PAYLOADS = {
    "himalayas": _himalayas,
    "remoteok": _remoteok,
    "remotive": _remotive,
    "weworkremotely": _weworkremotely,
}


def test_every_board_has_a_sample_payload() -> None:
    """Otherwise a new adapter silently opts out of every contract below by not being listed."""
    assert set(PAYLOADS) == {name for name, _ in BUILTINS}


@pytest.mark.parametrize("name,factory", BUILTINS, ids=[n for n, _ in BUILTINS])
class TestEveryBuiltinHonorsTheQueryItWasGiven:
    """The invariants nothing checked before.

    An adapter that ignored `scan_depth_per_source`, ignored `max_age_days`, forgot to count
    `scanned`, or stopped early while reporting a complete scan passed the whole suite. The last is
    the expensive one: `SearchResult.fully_scanned` is derived from `truncated`, so a board that
    gets it wrong tells an agent a partial run was complete, and the seeker is never told.
    """

    @staticmethod
    def _fetch(factory: object, count: int, age: int, **query: object) -> SourceResult:
        """Drive one board through a real fetch against its own dialect of sample payload."""
        source: JobSource = factory()  # type: ignore[operator]
        with respx.mock:
            PAYLOADS[source.name](count, age)
            return source.fetch(SearchQuery(**query))  # type: ignore[arg-type]

    def test_the_scan_depth_bounds_what_comes_back(self, name: str, factory: object) -> None:
        result = self._fetch(factory, count=10, age=1, scan_depth_per_source=3)
        assert not result.failed, result.error
        assert len(result.jobs) == 3

    def test_a_bounded_scan_is_reported_as_truncated(self, name: str, factory: object) -> None:
        """The lie that matters. Stopping early and claiming a complete scan makes a partial run
        indistinguishable from a whole one, everywhere downstream."""
        result = self._fetch(factory, count=10, age=1, scan_depth_per_source=3)
        assert result.truncated is True

    def test_scanned_counts_what_was_read_not_what_survived(
        self, name: str, factory: object
    ) -> None:
        result = self._fetch(factory, count=10, age=1, scan_depth_per_source=3)
        assert result.scanned >= len(result.jobs)

    def test_a_posting_older_than_the_age_window_does_not_come_back(
        self, name: str, factory: object
    ) -> None:
        result = self._fetch(factory, count=5, age=400, max_age_days=30)
        assert not result.failed, result.error
        assert result.jobs == []

    def test_the_same_postings_come_back_when_the_window_allows_them(
        self, name: str, factory: object
    ) -> None:
        """Guards the test above: an adapter that returned nothing for every query would pass it."""
        result = self._fetch(factory, count=5, age=400, max_age_days=None)
        assert len(result.jobs) == 5

    def test_it_reads_everything_when_the_depth_allows(self, name: str, factory: object) -> None:
        result = self._fetch(factory, count=4, age=1, scan_depth_per_source=50)
        assert len(result.jobs) == 4
        assert result.scanned >= 4

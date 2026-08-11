"""Covers `job_seeker.infrastructure.sources.base`.

Never touches the network: HTTP is mocked with respx, and the retry backoff uses an injected
sleep so the tests do not actually wait.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
import respx

from job_seeker.domain.models import CurrencySource, SalaryPeriod
from job_seeker.infrastructure.sources import base


class TestCleanHtml:
    def test_strips_tags_and_returns_text(self) -> None:
        assert base.clean_html("<p>Hello <strong>world</strong></p>") == "Hello world"

    def test_collapses_whitespace_and_trims(self) -> None:
        assert base.clean_html("<p>a</p>\n\n  <p>b</p>") == "a b"

    def test_decodes_entities(self) -> None:
        assert base.clean_html("<p>R&amp;D &lt;team&gt;</p>") == "R&D <team>"

    def test_empty_input_is_empty_output(self) -> None:
        assert base.clean_html("") == ""

    def test_plain_text_passes_through(self) -> None:
        assert base.clean_html("no markup here") == "no markup here"


class TestToUtcDatetime:
    def test_epoch_seconds_become_aware_utc(self) -> None:
        dt = base.to_utc_datetime(1_700_000_000)
        assert dt == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)
        assert dt is not None and dt.tzinfo is not None

    def test_none_stays_none(self) -> None:
        assert base.to_utc_datetime(None) is None

    def test_a_float_epoch_works(self) -> None:
        assert base.to_utc_datetime(1_700_000_000.0) is not None

    def test_a_nonsense_value_is_none_not_a_crash(self) -> None:
        """A board sending a garbage timestamp must not take down a whole fetch."""
        assert base.to_utc_datetime("not a number") is None  # type: ignore[arg-type]

    def test_a_bool_is_treated_as_absent_not_as_epoch_1(self) -> None:
        """bool subclasses int, so `pubDate: true` would otherwise become a 1970 date. It is
        absent data, not a timestamp, consistent with how the salary parser rejects bool."""
        assert base.to_utc_datetime(True) is None


class TestSalaryFromBounds:
    """Turning a board's two numbers into a SalaryRange, without ever raising.

    This lives here rather than in each adapter because it is not board knowledge: every board can
    send a negative, a NaN, or an upper bound below its lower one, and every adapter must survive
    it identically. Two adapters implementing it separately had already produced two different
    number formats.

    The load-bearing property is that it CANNOT raise. `SalaryRange` rejects a negative and an
    inverted range, `fetch` is contracted never to raise, and one bad row must not cost a board.
    """

    def test_ordinary_bounds_become_a_range(self) -> None:
        salary = base.salary_from_bounds(
            120_000,
            160_000,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum, salary.currency) == (120_000, 160_000, "USD")

    def test_no_figures_at_all_is_no_salary(self) -> None:
        assert (
            base.salary_from_bounds(
                None,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_zero_means_unspecified_on_every_board(self) -> None:
        """Both boards document 0 as "unspecified". Expressing that once stops the two adapters
        spelling it differently, which they did: one used truthiness, one a `> 0` predicate."""
        assert (
            base.salary_from_bounds(
                0,
                0,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )
        floor = base.salary_from_bounds(
            120_000,
            0,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert floor is not None and (floor.minimum, floor.maximum) == (120_000, None)

    @pytest.mark.parametrize(
        "value",
        [-1, -0.5, float("nan"), float("inf"), float("-inf"), "120k", True, None, [1]],
    )
    def test_an_unusable_figure_is_dropped_rather_than_raised_on(self, value: object) -> None:
        """The regression this exists to prevent: a negative or non-finite figure reached
        `SalaryRange`, whose `ge=0` raised ValidationError out of `_normalize`, out of `fetch`, and
        killed the entire board over one row. `inf` was worse, passing `ge=0` as a salary of
        infinity.
        """
        assert (
            base.salary_from_bounds(
                value,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )
        assert (
            base.salary_from_bounds(
                None,
                value,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_a_bool_is_not_a_salary(self) -> None:
        """bool subclasses int, so True would otherwise become a salary of 1."""
        assert (
            base.salary_from_bounds(
                True,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_an_inverted_range_is_kept_as_text_not_swapped_and_not_raised_on(self) -> None:
        salary = base.salary_from_bounds(
            200_000,
            100_000,
            currency="MXN",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (None, None)
        assert salary.currency == "MXN"
        assert "inverted range" in salary.note
        assert "withheld" in salary.note
        assert "200,000 to 100,000" in salary.note  # stated, not restated as a range

    def test_a_seven_figure_inverted_range_stays_readable(self) -> None:
        """The `,g` format spec used before this was extracted drops to scientific notation past
        six significant digits, so an inverted MXN range rendered as "1.5e+06 - 1e+06"."""
        salary = base.salary_from_bounds(
            1_500_000,
            1_000_000,
            currency="MXN",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert "1,500,000 to 1,000,000" in salary.note  # not "1.5e+06"

    def test_equal_bounds_are_a_fixed_rate_not_an_inversion(self) -> None:
        salary = base.salary_from_bounds(
            150_000,
            150_000,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (150_000, 150_000)


class TestRetryAfterIsAlwaysSleepable:
    """Whatever a board sends, the value handed to time.sleep must be one it accepts.

    The header is board-controlled and `fetch` runs in a ThreadPoolExecutor worker, so a value that
    raises (`inf` reaching sleep is an OverflowError) or blocks for years is a denial of service on
    our own run, not the board's problem. The individual cases are covered above; this asserts the
    invariant holds across every shape at once, so a future change to the parsing cannot satisfy
    one example while breaking the guarantee.

    Non-ASCII header values are absent on purpose: httpx refuses to construct them, so they cannot
    reach this code.
    """

    @pytest.mark.parametrize(
        "header",
        [
            "",
            " ",
            "0",
            "-0",
            "-99999",
            "999999999999",
            "inf",
            "-inf",
            "nan",
            "NaN",
            "1e400",
            "0x10",
            "1_000",
            "1,000",
            "+120",
            "  120  ",
            "soon",
            "12; 34",
            "2015-10-21T07:28:00Z",
            "Wed, 21 Oct 2015 07:28:00 GMT",
            "Fri, 31 Dec 9999 23:59:59 GMT",
            "Mon, 01 Jan 1900 00:00:00 GMT",
            "Fri, 99 Xxx 2100 99:99:99 GMT",
            "A" * 10_000,
        ],
    )
    def test_a_bounded_non_negative_float_comes_back_and_nothing_raises(self, header: str) -> None:
        response = httpx.Response(
            429,
            headers={"retry-after": header},
            request=httpx.Request("GET", "https://example.com/api"),
        )
        seconds = base._retry_after_seconds(response)
        assert isinstance(seconds, float)
        assert seconds == seconds  # not NaN, which sleep would reject
        assert not seconds < 0  # -0.0 is acceptable to sleep; a true negative is not
        assert seconds <= base._MAX_BACKOFF


class TestBuildClient:
    def test_sends_the_project_user_agent(self) -> None:
        with respx.mock:
            route = respx.get("https://example.com/x").mock(return_value=httpx.Response(200))
            with base.build_client() as client:
                client.get("https://example.com/x")
        assert "job-seeker" in route.calls.last.request.headers["user-agent"]

    def test_carries_a_timeout(self) -> None:
        with base.build_client(timeout=5.0) as client:
            assert client.timeout.read == 5.0


class TestGetJson:
    def test_returns_parsed_json(self) -> None:
        with respx.mock:
            respx.get("https://example.com/api").mock(
                return_value=httpx.Response(200, json={"ok": True})
            )
            with base.build_client() as client:
                assert base.get_json(client, "https://example.com/api") == {"ok": True}

    def test_passes_query_params(self) -> None:
        with respx.mock:
            route = respx.get("https://example.com/api").mock(
                return_value=httpx.Response(200, json=[])
            )
            with base.build_client() as client:
                base.get_json(client, "https://example.com/api", params={"offset": 40})
        assert route.calls.last.request.url.params["offset"] == "40"

    def test_a_huge_retry_after_is_capped_not_honored_verbatim(self) -> None:
        """The header is board-controlled and fetch runs in a worker thread. An uncapped
        "999999999" would hang the slot for years, so it clamps to the ceiling."""
        slept: list[float] = []
        with respx.mock:
            respx.get("https://example.com/api").mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "999999999"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with base.build_client() as client:
                base.get_json(client, "https://example.com/api", sleep=slept.append, max_retries=3)
        assert slept == [base._MAX_BACKOFF]

    def test_an_infinite_retry_after_clamps_instead_of_overflowing_sleep(self) -> None:
        """`Retry-After: inf` would make time.sleep raise OverflowError straight out of the
        adapter, breaking the never-raise contract. It must clamp to the ceiling instead."""
        slept: list[float] = []
        with respx.mock:
            respx.get("https://example.com/api").mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "inf"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with base.build_client() as client:
                base.get_json(client, "https://example.com/api", sleep=slept.append, max_retries=3)
        assert slept == [base._MAX_BACKOFF]

    def test_a_nan_retry_after_falls_back_to_the_default(self) -> None:
        slept: list[float] = []
        with respx.mock:
            respx.get("https://example.com/api").mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "nan"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with base.build_client() as client:
                base.get_json(client, "https://example.com/api", sleep=slept.append, max_retries=3)
        assert slept == [base._RATE_LIMIT_BACKOFF]

    def _sleeps_for(self, header: str | None) -> list[float]:
        """Drive one 429-then-200 exchange and report what it slept."""
        slept: list[float] = []
        headers = {} if header is None else {"retry-after": header}
        with respx.mock:
            respx.get("https://example.com/api").mock(
                side_effect=[
                    httpx.Response(429, headers=headers),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with base.build_client() as client:
                base.get_json(client, "https://example.com/api", sleep=slept.append, max_retries=3)
        return slept

    def test_a_429_with_no_retry_after_at_all_uses_the_default_backoff(self) -> None:
        """The ordinary 429. Most boards send no Retry-After, and every other test here sets one,
        so the plain case had never actually run."""
        assert self._sleeps_for(None) == [base._RATE_LIMIT_BACKOFF]

    def test_a_negative_retry_after_falls_back_rather_than_sleeping_backwards(self) -> None:
        assert self._sleeps_for("-5") == [base._RATE_LIMIT_BACKOFF]

    def test_an_unparseable_retry_after_falls_back_to_the_default(self) -> None:
        assert self._sleeps_for("soon") == [base._RATE_LIMIT_BACKOFF]

    def test_an_http_date_retry_after_is_honored_not_ignored(self) -> None:
        """RFC 9110 lets Retry-After be an HTTP-date as well as a number of seconds, and boards
        send both. Reading only the numeric form means a board asking for a real pause gets
        retried on the 2-second default instead, which is the impolite direction to be wrong in."""
        soon = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
        slept = self._sleeps_for(soon)
        assert len(slept) == 1
        assert 20 < slept[0] <= 40  # ~30s, tolerant of clock movement during the test

    def test_a_far_future_http_date_still_clamps_to_the_ceiling(self) -> None:
        """The clamp is the whole reason the header is not trusted: it is board-controlled and
        fetch runs in a worker thread."""
        assert self._sleeps_for("Fri, 31 Dec 2100 23:59:59 GMT") == [base._MAX_BACKOFF]

    def test_an_http_date_already_in_the_past_falls_back_instead_of_going_negative(self) -> None:
        assert self._sleeps_for("Wed, 21 Oct 2015 07:28:00 GMT") == [base._RATE_LIMIT_BACKOFF]

    def test_an_http_date_with_no_timezone_is_read_as_gmt_not_crashed_on(self) -> None:
        """An HTTP-date is GMT by definition, but a sloppy board can omit the zone and the parser
        accepts it, yielding a naive datetime. Subtracting that from an aware `now` raises
        TypeError, straight out of a worker thread."""
        naive = format_datetime(datetime.now(UTC) + timedelta(seconds=30))[:-6]  # drop " +0000"
        slept = self._sleeps_for(naive)
        assert len(slept) == 1
        assert 20 < slept[0] <= 40

    def test_backs_off_and_retries_on_429_then_succeeds(self) -> None:
        slept: list[float] = []
        with respx.mock:
            respx.get("https://example.com/api").mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "2"}),
                    httpx.Response(200, json={"ok": True}),
                ]
            )
            with base.build_client() as client:
                result = base.get_json(
                    client, "https://example.com/api", sleep=slept.append, max_retries=3
                )
        assert result == {"ok": True}
        assert slept == [2.0]  # honored Retry-After

    def test_gives_up_after_max_retries_on_persistent_429(self) -> None:
        with respx.mock:
            respx.get("https://example.com/api").mock(
                return_value=httpx.Response(429, headers={"retry-after": "1"})
            )
            with base.build_client() as client, pytest.raises(httpx.HTTPStatusError):
                base.get_json(
                    client, "https://example.com/api", sleep=lambda _: None, max_retries=2
                )

    def test_raises_on_a_non_429_error_status(self) -> None:
        with respx.mock:
            respx.get("https://example.com/api").mock(return_value=httpx.Response(500))
            with base.build_client() as client, pytest.raises(httpx.HTTPStatusError):
                base.get_json(client, "https://example.com/api", sleep=lambda _: None)

    def test_a_200_with_a_non_json_body_raises_an_httpx_error_not_a_valueerror(self) -> None:
        """A board can return 200 with an HTML challenge page. The raw json() would raise
        JSONDecodeError (a ValueError), which an adapter's httpx.HTTPError catch misses. Wrapping
        it as DecodingError (an HTTPError) is what lets the adapter report the failure instead of
        crashing a worker."""
        with respx.mock:
            respx.get("https://example.com/api").mock(
                return_value=httpx.Response(200, text="<html>nope</html>")
            )
            with base.build_client() as client, pytest.raises(httpx.HTTPError):
                # HTTPError is the base class of DecodingError
                base.get_json(client, "https://example.com/api", sleep=lambda _: None)


class TestGetXml:
    """The RSS side of the same transport. What matters is that it is the *same* transport: the
    politeness lives in one place and an RSS board inherits it rather than reimplementing it."""

    _FEED = "<rss version='2.0'><channel><item><title>x</title></item></channel></rss>"

    def test_returns_a_parsed_document(self) -> None:
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(200, text=self._FEED)
            )
            with base.build_client() as client:
                document = base.get_xml(client, "https://example.com/feed.rss")
        items = document.find_all("item")
        assert [base.element_text(item, "title") for item in items] == ["x"]

    def test_it_honors_the_declared_encoding_rather_than_the_charset_header(self) -> None:
        """Parsed from bytes, so the XML declaration wins. Served as latin-1 while the header says
        utf-8, reading `.text` would turn a Spanish company name into mojibake."""
        body = "<?xml version='1.0' encoding='ISO-8859-1'?><rss><item><title>Añadir</title></item></rss>"
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(
                    200,
                    content=body.encode("latin-1"),
                    headers={"content-type": "application/rss+xml; charset=utf-8"},
                )
            )
            with base.build_client() as client:
                document = base.get_xml(client, "https://example.com/feed.rss")
        assert base.element_text(document, "title") == "Añadir"

    def test_backs_off_and_retries_on_429_the_same_way_the_json_path_does(self) -> None:
        """The reason the transport was split out. Before it, this politeness existed only inside
        `get_json` and the first RSS board would have had to grow its own."""
        slept: list[float] = []
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                side_effect=[
                    httpx.Response(429, headers={"retry-after": "2"}),
                    httpx.Response(200, text=self._FEED),
                ]
            )
            with base.build_client() as client:
                base.get_xml(
                    client, "https://example.com/feed.rss", sleep=slept.append, max_retries=3
                )
        assert slept == [2.0]

    def test_a_document_that_is_not_what_the_caller_came_for_raises(self) -> None:
        """A board that switches format still answers 200 with valid XML. Without this the adapter
        reads elements that are no longer there and reports a clean run that found no jobs."""
        atom = "<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>x</title></entry></feed>"
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(200, text=atom)
            )
            with base.build_client() as client, pytest.raises(httpx.HTTPError):
                base.get_xml(
                    client, "https://example.com/feed.rss", root="rss", sleep=lambda _: None
                )

    def test_the_document_it_did_come_for_passes(self) -> None:
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(200, text=self._FEED)
            )
            with base.build_client() as client:
                assert base.get_xml(client, "https://example.com/feed.rss", root="rss") is not None

    def test_raises_on_a_non_429_error_status(self) -> None:
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(return_value=httpx.Response(503))
            with base.build_client() as client, pytest.raises(httpx.HTTPStatusError):
                base.get_xml(client, "https://example.com/feed.rss", sleep=lambda _: None)

    @pytest.mark.parametrize(
        "body",
        [
            "<!doctype html><html><body>Just a moment...</body></html>",
            "<html><body>Just a moment...</body></html>",
            "",
            "not xml at all",
        ],
        ids=["challenge page", "challenge page with no doctype", "empty", "prose"],
    )
    def test_a_200_that_is_not_xml_raises_an_httpx_error(self, body: str) -> None:
        """Where `json()` raises on a challenge page, the XML parser finds a way to accept one: with
        a doctype it yields a document holding nothing, and without one it parses into an `html`
        root, which is a web page and not the feed that was asked for. Untreated, the adapter
        reports a clean run that found no jobs, the silent-failure shape `SourceCoverage` exists to
        prevent."""
        with respx.mock:
            respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(200, text=body)
            )
            with base.build_client() as client, pytest.raises(httpx.HTTPError):
                base.get_xml(client, "https://example.com/feed.rss", sleep=lambda _: None)

    def test_passes_query_params(self) -> None:
        with respx.mock:
            route = respx.get("https://example.com/feed.rss").mock(
                return_value=httpx.Response(200, text=self._FEED)
            )
            with base.build_client() as client:
                base.get_xml(client, "https://example.com/feed.rss", params={"page": 2})
        assert route.calls.last.request.url.params["page"] == "2"


class TestToUtcFromEmailDate:
    def test_it_reads_an_rss_pubdate(self) -> None:
        assert base.to_utc_from_email_date("Tue, 11 Aug 2026 16:03:20 +0000") == datetime(
            2026, 8, 11, 16, 3, 20, tzinfo=UTC
        )

    def test_it_converts_a_non_utc_offset_rather_than_dropping_it(self) -> None:
        assert base.to_utc_from_email_date("Tue, 11 Aug 2026 16:03:20 -0500") == datetime(
            2026, 8, 11, 21, 3, 20, tzinfo=UTC
        )

    def test_a_date_with_no_zone_is_read_as_utc_and_stays_aware(self) -> None:
        """A naive datetime compared against an aware `now` raises during age filtering, which is
        single-threaded and takes the whole run with it."""
        parsed = base.to_utc_from_email_date("Tue, 11 Aug 2026 16:03:20")
        assert parsed is not None and parsed.tzinfo is not None

    @pytest.mark.parametrize("value", ["", "   ", "yesterday", "2026-08-11T16:03:20"])
    def test_an_unreadable_value_is_none_rather_than_an_exception(self, value: str) -> None:
        """One malformed record must not cost the page of good ones around it."""
        assert base.to_utc_from_email_date(value) is None

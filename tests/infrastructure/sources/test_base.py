"""Covers `job_seeker.infrastructure.sources.base`.

Never touches the network: HTTP is mocked with respx, and the retry backoff uses an injected
sleep so the tests do not actually wait.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

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

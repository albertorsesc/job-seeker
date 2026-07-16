"""Shared HTTP and normalization helpers for source adapters.

This is the only place in the sources package that knows about HTTP and HTML. An adapter uses
these to fetch and clean, then spends its own code on the one thing it cannot share: turning a
board's particular payload into canonical `Job`s.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from job_seeker import __version__

USER_AGENT = f"job-seeker/{__version__} (+https://github.com/albertorsesc/job-seeker)"

# Connection errors get one transport-level retry; HTTP 429 is handled in get_json, because a
# rate-limit is a response, not a connection failure, and it carries a Retry-After to honor.
_DEFAULT_TIMEOUT = 15.0
_CONNECT_RETRIES = 1
_RATE_LIMIT_BACKOFF = 2.0  # seconds, when a 429 gives no Retry-After
# Hard ceiling on any honored Retry-After. The header is board-controlled, and fetch runs in a
# ThreadPoolExecutor worker: an uncapped value ("999999999", "inf") would either hang a slot for
# years or, with inf, make time.sleep raise OverflowError straight out of fetch, breaking the
# never-raise contract. A board that truly wants a longer pause gets retried on the next run.
_MAX_BACKOFF = 60.0


def build_client(timeout: float = _DEFAULT_TIMEOUT) -> httpx.Client:
    """A configured client. Polite by default: identifies itself and does not hang forever."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(timeout),
        transport=httpx.HTTPTransport(retries=_CONNECT_RETRIES),
        follow_redirects=True,
    )


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """GET and parse JSON, backing off and retrying on HTTP 429 only.

    A 429 is the board asking us to slow down, so it is retried after its Retry-After (or a
    default backoff). Every other error status raises `HTTPStatusError`: the adapter catches it
    and reports the failure in its `SourceResult`, because one board failing must not abort a run
    across the others. `sleep` is injectable so tests do not actually wait.
    """
    if sleep is None:
        import time

        sleep = time.sleep

    attempt = 0
    while True:
        response = client.get(url, params=params)
        if response.status_code == 429 and attempt < max_retries:
            sleep(_retry_after_seconds(response))
            attempt += 1
            continue
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            # A 200 with a non-JSON body: a Cloudflare challenge, a maintenance page, a
            # truncated response. json() raises JSONDecodeError, a ValueError disjoint from
            # httpx.HTTPError, so it would escape an adapter's error handling and violate the
            # never-raise contract. Re-raise as DecodingError (an HTTPError) so the adapter's
            # existing catch turns it into a reported failure, for this adapter and every future
            # one.
            raise httpx.DecodingError(
                f"non-JSON response from {url}", request=response.request
            ) from exc


def _retry_after_seconds(response: httpx.Response) -> float:
    """Seconds to wait after a 429: the board's Retry-After when sane, always capped.

    Clamped to `_MAX_BACKOFF` so a hostile or buggy header cannot hang a worker. NaN and negative
    values fall back to the default; `inf` clamps to the ceiling rather than reaching time.sleep.
    """
    header = response.headers.get("retry-after", "")
    try:
        seconds = float(header)
    except ValueError:
        return _RATE_LIMIT_BACKOFF
    if seconds != seconds or seconds < 0:  # NaN (self-inequality) or negative
        return _RATE_LIMIT_BACKOFF
    return min(seconds, _MAX_BACKOFF)


def clean_html(html: str) -> str:
    """Plain text from an HTML fragment: tags stripped, entities decoded, whitespace collapsed.

    Board descriptions are HTML. The scorer and reporters want words, not markup, and a
    collapsed single-spaced string keeps a regex from tripping over stray newlines.
    """
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return " ".join(text.split())


def to_utc_datetime(epoch: int | float | None) -> datetime | None:
    """A Unix timestamp as a timezone-aware UTC datetime, or None if it is absent or unparseable.

    Aware, always: `posted_at` is compared against `now` for age filtering, and mixing a naive
    and an aware datetime raises at runtime. A garbage value returns None rather than crashing,
    because one malformed record must not take down a whole page of good ones.
    """
    # bool is a subclass of int, so `True` would otherwise become a 1970 date. Exclude it, the
    # same way the salary parser does, so a `pubDate: true` is treated as absent, not as an epoch.
    if epoch is None or isinstance(epoch, bool):
        return None
    try:
        return datetime.fromtimestamp(float(epoch), tz=UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None

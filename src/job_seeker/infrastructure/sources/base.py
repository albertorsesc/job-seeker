"""Shared HTTP and normalization helpers for source adapters.

This is the only place in the sources package that knows about HTTP and HTML. An adapter uses
these to fetch and clean, then spends its own code on the one thing it cannot share: turning a
board's particular payload into canonical `Job`s.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
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

    Clamped to `_MAX_BACKOFF` so a hostile or buggy header cannot hang a worker. Anything that is
    not a readable future instant falls back to the default: absent (the common case, since most
    boards send a bare 429), malformed, NaN, negative, or a date already past. `inf` clamps to the
    ceiling rather than reaching time.sleep.
    """
    seconds = _retry_after_delay(response.headers.get("retry-after", ""))
    if seconds is None or seconds != seconds or seconds < 0:  # unreadable, NaN, or already past
        return _RATE_LIMIT_BACKOFF
    return min(seconds, _MAX_BACKOFF)


def _retry_after_delay(header: str) -> float | None:
    """Retry-After as seconds from now, in either form RFC 9110 permits, or None if unreadable.

    The spec allows delay-seconds ("120") or an HTTP-date ("Wed, 21 Oct 2015 07:28:00 GMT"), and
    boards send both. Reading only the number meant a board asking for a real pause was retried on
    the two-second default instead, which is the impolite direction to be wrong in for a scraper
    that identifies itself and means it.
    """
    header = header.strip()
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return None
    # An HTTP-date is GMT by definition, but a sloppy one can parse with no zone at all, and
    # subtracting a naive datetime from an aware one raises.
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when - datetime.now(UTC)).total_seconds()


def clean_html(html: str) -> str:
    """Plain text from an HTML fragment: tags stripped, entities decoded, whitespace collapsed.

    Board descriptions are HTML. The scorer and reporters want words, not markup, and a
    collapsed single-spaced string keeps a regex from tripping over stray newlines.
    """
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(separator=" ")
    return " ".join(text.split())


def age_cutoff(max_age_days: int | None) -> datetime | None:
    """The oldest acceptable posting time, or None when no age window is set."""
    if max_age_days is None:
        return None
    return datetime.now(UTC) - timedelta(days=max_age_days)


def is_stale(posted_at: datetime | None, cutoff: datetime | None) -> bool:
    """Whether a posting is older than the window. An undated posting is never stale: age cannot
    be judged, so it is kept and the seeker decides."""
    if cutoff is None or posted_at is None:
        return False
    return posted_at < cutoff


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

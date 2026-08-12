"""Shared HTTP and normalization helpers for source adapters.

The wire, and how to read what comes off it: HTTP with its politeness, JSON and XML decoding,
markup, and dates. An adapter uses these to fetch and clean, then spends its own code on the one thing it cannot share: turning a
board's particular payload into canonical `Job`s.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup, Tag

from job_seeker import __version__

USER_AGENT = f"job-seeker/{__version__} (+https://github.com/albertorsesc/job-seeker)"

# Connection errors get one transport-level retry; HTTP 429 is handled in `_get`, because a
# rate-limit is a response, not a connection failure, and it carries a Retry-After to honor.
_DEFAULT_TIMEOUT = 15.0
_CONNECT_RETRIES = 1
_RATE_LIMIT_BACKOFF = 2.0  # seconds, when a 429 gives no Retry-After
# Hard ceiling on any honored Retry-After. The header is board-controlled, and fetch runs in a
# ThreadPoolExecutor worker: an uncapped value ("999999999", "inf") would either hang a slot for
# years or, with inf, make time.sleep raise OverflowError straight out of fetch, breaking the
# never-raise contract. A board that truly wants a longer pause gets retried on the next run.
#
# It bounds one wait, not the run: `max_retries` defaults to 2, so a board that keeps answering 429
# with a long Retry-After holds its worker for two minutes. Nothing in production shortens that,
# since `fetch` injects no `sleep`.
_MAX_BACKOFF = 60.0


def build_client(timeout: float = _DEFAULT_TIMEOUT) -> httpx.Client:
    """A configured client. Polite by default: identifies itself and does not hang forever."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx.Timeout(timeout),
        transport=httpx.HTTPTransport(retries=_CONNECT_RETRIES),
        follow_redirects=True,
    )


def _get(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int,
    sleep: Callable[[float], None] | None,
) -> httpx.Response:
    """GET a successful response, backing off and retrying on HTTP 429 only.

    The politeness, with no opinion about what the body contains. A 429 is the board asking us to
    slow down, so it is retried after its Retry-After (or a default backoff). Every other error
    status raises `HTTPStatusError`: the adapter catches it and reports the failure in its
    `SourceResult`, because one board failing must not abort a run across the others. `sleep` is
    injectable so tests do not actually wait.

    Separate from the decoders because a board's transport and its payload format are independent:
    the RSS boards need this exact backoff and parse XML, and welding it into `get_json` meant the
    first of them would either copy the retry loop or grow a second one beside it.
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
        return response


def get_json(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """GET and parse JSON. See `_get` for the retry behaviour."""
    response = _get(client, url, params=params, max_retries=max_retries, sleep=sleep)
    try:
        return response.json()
    except ValueError as exc:
        # A 200 with a non-JSON body: a Cloudflare challenge, a maintenance page, a truncated
        # response. json() raises JSONDecodeError, a ValueError disjoint from httpx.HTTPError, so
        # it would escape an adapter's error handling and violate the never-raise contract.
        # Re-raise as DecodingError (an HTTPError) so the adapter's existing catch turns it into a
        # reported failure, for this adapter and every future one.
        raise httpx.DecodingError(
            f"non-JSON response from {url}", request=response.request
        ) from exc


def get_xml(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    root: str | None = None,
    max_retries: int = 2,
    sleep: Callable[[float], None] | None = None,
) -> BeautifulSoup:
    """GET and parse an XML document, such as an RSS feed. See `_get` for the retry behaviour.

    Parsed from `.content` rather than `.text`: an XML declaration names its own encoding, and
    handing the parser bytes lets it honor that instead of second-guessing httpx's charset.

    Rejecting what came back is what makes this as safe as `get_json`. Where `json()` raises on a
    Cloudflare challenge page, the XML parser takes one of two views of one and neither is an
    error: a page with a doctype yields a document holding nothing, and a well-formed one parses
    happily into an `html` root, which is a web page rather than the feed that was asked for.

    `root` is how a caller says which document it came for ("rss"), and it covers the other half of
    the same problem: a board that switches format still answers 200 with valid XML, and an adapter
    reading elements that are no longer there would report a clean run that found no jobs. That is
    the silent-failure shape `SourceCoverage` exists to prevent, so it is a `DecodingError` like
    the rest, caught by the adapter's existing `except httpx.HTTPError`.
    """
    response = _get(client, url, params=params, max_retries=max_retries, sleep=sleep)
    document = BeautifulSoup(response.content, "xml")
    found = document.find()
    name = found.name.lower() if isinstance(found, Tag) else None
    if name is None or name == "html":
        raise httpx.DecodingError(f"non-XML response from {url}", request=response.request)
    if root is not None and name != root.lower():
        raise httpx.DecodingError(
            f"expected a {root} document from {url}, got {name}", request=response.request
        )
    return document


def element_text(node: Tag, name: str) -> str:
    """The stripped text of one child element, or "" when the document does not carry it.

    `find` returns `Tag | NavigableString | None`, so reading a field straight would be three lines
    of narrowing at every use, in adapters whose job is a board's dialect rather than bs4's type
    surface.

    Missing and empty both give "". A feed omits a field and emits it blank interchangeably, and
    where that distinction carries meaning it is the adapter that knows it: it reads the "" and
    says what its own board means by it.
    """
    found = node.find(name)
    return found.get_text().strip() if isinstance(found, Tag) else ""


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
    when = to_utc_from_email_date(header)
    if when is None:
        return None
    return (when - datetime.now(UTC)).total_seconds()


def to_utc_from_email_date(value: str) -> datetime | None:
    """An RFC 2822 date as an aware UTC datetime, or None if it is absent or unreadable.

    One format, two callers, because two specifications point at the same grammar: an RSS
    `pubDate` and an HTTP `Retry-After` date are both "Tue, 11 Aug 2026 16:03:20 +0000".

    Aware, always. The zone is part of the format, but a sloppy sender can emit one without it,
    and a naive datetime compared against an aware `now` raises during age filtering, taking a
    whole run with it. An unreadable value is None rather than an exception, so one malformed
    record cannot cost a page of good ones.
    """
    value = value.strip()
    if not value:
        return None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is the one that is easy to miss: it is not a ValueError, and it is what an
        # 11-digit year or zone offset raises when the parsed int will not fit the C int the
        # datetime constructor wants. Uncaught, one malformed record costs the whole board, and on
        # the Retry-After path it escapes `_get` past every `except httpx.HTTPError` above it.
        return None
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


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

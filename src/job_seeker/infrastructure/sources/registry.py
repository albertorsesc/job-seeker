"""The board registry: the one place the system learns that a job board exists.

This is the open/closed seam. Adding a board is a new adapter plus one `register(...)` call
here. Nothing in the domain, the application, the CLI or the MCP server changes, because none
of them names a board: they ask the registry for `JobSource`s and talk to the Protocol.

Factories rather than instances, so registration stays free. A board is only constructed when
someone actually intends to use it, which keeps `job-seeker sources` fast and keeps an adapter
with an expensive constructor from taxing every run that ignores it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from job_seeker.application.ports import JobSource

SourceFactory = Callable[[], JobSource]
"""Builds one board.

**Must not raise, and must not do I/O.** A board that cannot run (missing optional dependency,
absent credential) constructs anyway and reports it through `is_available()`. Construction is not
the place to discover the world is broken: it happens before anyone has said what they want, and
the whole point of `is_available()` is to make "cannot run" a fact you can read rather than an
exception you have to catch.
"""

# Module-level and mutable on purpose: a registry is the one place where that is the point
# rather than a smell. Kept private so registration goes through `register`, which is what
# makes the duplicate-name check possible.
#
# **Register at import time only. Reads are concurrent.** The orchestrator fans sources out
# across a ThreadPoolExecutor and only ever reads. Registration happening under the import lock
# is what makes that safe without a lock of its own; registering from a worker thread would make
# `sorted(_FACTORIES)` liable to "dictionary changed size during iteration". This invariant is
# load-bearing, which is why it is written down rather than assumed.
_FACTORIES: dict[str, SourceFactory] = {}


@dataclass(frozen=True)
class SourceStatus:
    """Whether one board can run, and why not. `error` is set only when the adapter is broken."""

    name: str
    available: bool
    error: str = ""


def register(name: str, factory: SourceFactory) -> None:
    """Make a board known to the system.

    Raises on a duplicate name rather than overwriting. Two adapters claiming "remotive" is a
    packaging mistake, and silently keeping the last one registered would surface later as a
    board that inexplicably returns nothing.
    """
    if name in _FACTORIES:
        raise ValueError(f"a source named {name!r} is already registered")
    _FACTORIES[name] = factory


def names() -> list[str]:
    """Every registered board name, sorted. Constructs nothing."""
    return sorted(_FACTORIES)


def create(name: str) -> JobSource:
    """Construct one board by name."""
    try:
        factory = _FACTORIES[name]
    except KeyError:
        known = ", ".join(names()) or "none"
        raise KeyError(f"no source named {name!r} is registered. Known sources: {known}") from None
    return factory()


def create_all() -> list[JobSource]:
    """Construct every registered board, in name order.

    Fail-fast on purpose: a factory that raises breaks its documented contract, so it is an
    adapter bug and the caller should see it. Callers that must survive a broken adapter want
    `describe()` instead.
    """
    return [_FACTORIES[name]() for name in names()]


def describe() -> list[SourceStatus]:
    """Report every board and whether it can run, isolating per-board failure.

    A factory or an `is_available()` that raises breaks the contract and is an adapter bug. But
    this feeds `job-seeker sources`, which is the command you run *because* something is broken.
    One bad adapter blinding you to the other five is the opposite of what that command is for,
    so the failure is reported against its own board and the rest still answer.
    """
    statuses: list[SourceStatus] = []
    for name in names():
        try:
            statuses.append(SourceStatus(name=name, available=_FACTORIES[name]().is_available()))
        except Exception as exc:  # an adapter bug, reported rather than propagated
            statuses.append(
                SourceStatus(name=name, available=False, error=f"{type(exc).__name__}: {exc}")
            )
    return statuses

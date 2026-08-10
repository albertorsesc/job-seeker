"""Covers `job_seeker.infrastructure.entrypoints.search`, the shared run wiring.

Both the CLI and the MCP server call this, so the sources-and-run path lives here once rather than
drifting between two entrypoints (the mistake the CLI/MCP `sources` divergence already made).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from job_seeker.domain.models import SearchQuery
from job_seeker.domain.profile import LocationProfile, Profile
from job_seeker.infrastructure.entrypoints.search import execute_search
from job_seeker.infrastructure.sources import defaults, registry

from ..conftest import FakeSource


@pytest.fixture(autouse=True)
def _no_builtin_boards(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Blank the built-ins so these tests see only the fakes they register.

    Deliberately NOT named `_isolated_registry`. pytest resolves fixtures by name with the closest
    definition winning, so a same-named autouse fixture here would shadow the shared one in
    `tests/infrastructure/conftest.py` and that one would not run at all. It did, until this
    rename. Nothing failed, because this fixture happened to repeat what the shared one does; the
    cost was that anything added to the shared fixture later would silently skip this module.
    """
    monkeypatch.setattr(defaults, "_BUILTINS", {})
    yield


def _profile() -> Profile:
    return Profile(location=LocationProfile(country="Testland"), search_terms=["Engineer"])


def _register(name: str) -> None:
    registry.register(name, lambda n=name: FakeSource(n))  # type: ignore[misc]


class TestExecuteSearch:
    def test_runs_every_registered_source_by_default(self) -> None:
        _register("a")
        _register("b")
        result = execute_search(_profile(), SearchQuery(), source_names=None)
        assert {c.source for c in result.coverage} == {"a", "b"}

    def test_a_source_filter_selects_a_subset(self) -> None:
        _register("a")
        _register("b")
        result = execute_search(_profile(), SearchQuery(), source_names=["a"])
        assert {c.source for c in result.coverage} == {"a"}

    def test_an_unknown_source_name_is_a_clear_error(self) -> None:
        _register("a")
        with pytest.raises(ValueError, match="nope"):
            execute_search(_profile(), SearchQuery(), source_names=["nope"])

    def test_no_registered_sources_is_a_clear_error(self) -> None:
        with pytest.raises(ValueError, match="[Nn]o .*source"):
            execute_search(_profile(), SearchQuery(), source_names=None)


def _raises_runtime_error() -> FakeSource:
    raise RuntimeError("credentials file not found")


def _raises_value_error() -> FakeSource:
    # A factory that raises ValueError specifically: pydantic's ValidationError is one, and an
    # adapter validating its config on construction is the ordinary way to produce it.
    raise ValueError("token must not be empty")


class TestABoardThatCannotBeConstructed:
    """A board whose factory raises must not end the run.

    `job-seeker sources` already reports a broken adapter and carries on, because `describe()`
    isolates per board. `find` used to traceback on that same adapter: two commands, opposite
    behaviour, one board. A board that cannot be built is the same class of event as a board that
    is down, and the run already survives that by reporting it in `SourceCoverage`.
    """

    def test_the_run_survives_and_the_failure_lands_in_coverage(self) -> None:
        registry.register("broken", _raises_runtime_error)
        result = execute_search(_profile(), SearchQuery(), source_names=None)
        broken = next(c for c in result.coverage if c.source == "broken")
        assert broken.failed
        assert "RuntimeError" in broken.error
        assert "credentials file not found" in broken.error

    def test_a_partial_run_is_not_reported_as_complete(self) -> None:
        """The whole point of coverage: a run missing a board must not look healthy."""
        registry.register("broken", _raises_runtime_error)
        result = execute_search(_profile(), SearchQuery(), source_names=None)
        assert not (result.all_sources_ran and result.fully_scanned)

    def test_the_healthy_boards_still_return_their_jobs(self) -> None:
        _register("working")
        registry.register("broken", _raises_runtime_error)
        result = execute_search(_profile(), SearchQuery(), source_names=None)
        working = next(c for c in result.coverage if c.source == "working")
        assert not working.failed
        assert {c.source for c in result.coverage} == {"working", "broken"}

    def test_a_value_error_from_a_factory_is_not_confused_with_a_bad_source_name(self) -> None:
        """The two used to collapse: `except ValueError` around the search caught both, so an
        adapter's own validation error was printed as if the seeker had typo'd `--sources`."""
        registry.register("broken", _raises_value_error)
        result = execute_search(_profile(), SearchQuery(), source_names=None)
        broken = next(c for c in result.coverage if c.source == "broken")
        assert broken.failed
        assert "token must not be empty" in broken.error

    def test_an_unknown_name_still_raises_rather_than_becoming_coverage(self) -> None:
        """A typo is not a broken board. It must still be refused, not reported as a failed
        source, or `--sources himalyas` would look like a board outage."""
        _register("a")
        with pytest.raises(ValueError, match="nope"):
            execute_search(_profile(), SearchQuery(), source_names=["nope"])

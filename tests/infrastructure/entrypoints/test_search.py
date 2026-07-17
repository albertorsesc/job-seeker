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
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Empty the registry AND the built-ins list, so these tests see only the fakes they register.
    # execute_search calls register_builtins(), which would otherwise re-add the real himalayas.
    monkeypatch.setattr(registry, "_FACTORIES", {})
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

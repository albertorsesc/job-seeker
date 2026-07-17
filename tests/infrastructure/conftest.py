"""Fixtures for the infrastructure layer.

Not in `tests/conftest.py`: the shared conftest is inherited by the domain and application
tests, and those must not import infrastructure. `tests/test_architecture.py` enforces that, so
putting the registry fixture at the root would fail the build. The rule is the reason this file
exists.
"""

from __future__ import annotations

import pytest

from job_seeker.domain.models import Job, SearchQuery, SourceResult
from job_seeker.infrastructure.sources import registry


class FakeSource:
    """An in-memory board. Imports no port and inherits nothing: conformance is structural.

    Shared rather than copy-pasted per test module. Three near-identical fakes had already
    started to drift, and a test double that disagrees with itself about the port is worse than
    no double at all.
    """

    def __init__(
        self, name: str = "fake", available: bool = True, jobs: list[Job] | None = None
    ) -> None:
        self._name = name
        self._available = available
        self._jobs = jobs or []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return self._available

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        return SourceResult(source=self._name, jobs=list(self._jobs), scanned=len(self._jobs))


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test its own empty registry.

    Swaps the dict rather than pruning what a test added. That distinction is the whole point:
    real adapters will register at import time, and a fixture that only removes what its own
    test registered still leaves those visible. Every assertion about "no boards are registered"
    or "names are exactly these" would then break the day the first board lands, and the natural
    reaction is to weaken the assertions rather than fix the isolation.
    """
    monkeypatch.setattr(registry, "_FACTORIES", {})

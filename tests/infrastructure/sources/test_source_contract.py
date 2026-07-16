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

import httpx
import pytest
import respx

from job_seeker.domain.models import SearchQuery
from job_seeker.infrastructure.sources import defaults

BUILTINS = list(defaults._BUILTINS.items())


@pytest.mark.parametrize("name,factory", BUILTINS, ids=[n for n, _ in BUILTINS])
class TestEveryBuiltinHonorsTheContract:
    def test_the_factory_constructs_without_raising(self, name: str, factory: object) -> None:
        factory()  # type: ignore[operator]

    def test_name_matches_the_registration_key(self, name: str, factory: object) -> None:
        assert factory().name == name  # type: ignore[operator]

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

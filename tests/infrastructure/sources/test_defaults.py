"""Covers `job_seeker.infrastructure.sources.defaults`.

The `_isolated_registry` autouse fixture gives each test an empty registry, so these assert
about `register_builtins` in isolation rather than about global process state.
"""

from __future__ import annotations

from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.defaults import register_builtins


class TestRegisterBuiltins:
    def test_registers_himalayas(self) -> None:
        register_builtins()
        assert "himalayas" in registry.names()

    def test_the_registered_factory_builds_a_working_source(self) -> None:
        register_builtins()
        source = registry.create("himalayas")
        assert source.name == "himalayas"
        assert source.is_available() is True

    def test_is_safe_to_call_twice(self) -> None:
        """The CLI and the MCP entrypoint both call it; a second call must not raise on the
        registry's duplicate-name guard."""
        register_builtins()
        register_builtins()
        assert registry.names().count("himalayas") == 1

    def test_does_not_clobber_a_name_someone_else_registered(self) -> None:
        """If a test or a plugin already claimed a built-in name, registration leaves it alone
        rather than raising, because register_builtins is a convenience, not an authority."""

        class Fake:
            name = "himalayas"

            def is_available(self) -> bool:
                return False

            def fetch(self, query: SearchQuery, /) -> SourceResult:
                return SourceResult(source="himalayas")

        registry.register("himalayas", Fake)
        register_builtins()  # must not raise
        assert registry.create("himalayas").is_available() is False

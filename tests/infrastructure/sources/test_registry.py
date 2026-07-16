"""Covers `job_seeker.infrastructure.sources.registry`.

`_isolated_registry` in `tests/infrastructure/conftest.py` gives each test an empty registry, so
these assertions survive the day a real board registers at import time.
"""

from __future__ import annotations

import pytest

from job_seeker.domain.models import SearchQuery, SourceResult
from job_seeker.infrastructure.sources import registry

from ..conftest import FakeSource


class TestRegister:
    def test_a_registered_source_becomes_known(self) -> None:
        registry.register("fake", FakeSource)
        assert "fake" in registry.names()

    def test_registering_a_duplicate_name_raises(self) -> None:
        """Two adapters claiming one name is a packaging mistake. Overwriting would surface
        later as a board that inexplicably returns nothing."""
        registry.register("fake", FakeSource)
        with pytest.raises(ValueError, match="already registered"):
            registry.register("fake", FakeSource)

    def test_names_are_sorted_so_output_is_stable(self) -> None:
        registry.register("zulu", FakeSource)
        registry.register("alpha", FakeSource)
        assert registry.names() == ["alpha", "zulu"]


class TestCreate:
    def test_creates_the_registered_source(self) -> None:
        registry.register("fake", FakeSource)
        assert registry.create("fake").name == "fake"

    def test_an_unknown_name_raises_and_says_what_is_known(self) -> None:
        registry.register("himalayas", FakeSource)
        with pytest.raises(KeyError, match="himalayas"):
            registry.create("hymalayas")

    def test_registration_constructs_nothing(self) -> None:
        """Factories, not instances: an adapter with an expensive constructor must not tax a
        run that never uses it."""
        built: list[str] = []

        def factory() -> FakeSource:
            built.append("built")
            return FakeSource()

        registry.register("lazy", factory)
        assert built == []
        registry.create("lazy")
        assert built == ["built"]


class TestCreateAll:
    def test_is_empty_when_nothing_is_registered(self) -> None:
        assert registry.create_all() == []

    def test_builds_every_registered_source_in_name_order(self) -> None:
        registry.register("zulu", lambda: FakeSource("zulu"))
        registry.register("alpha", lambda: FakeSource("alpha"))
        assert [s.name for s in registry.create_all()] == ["alpha", "zulu"]

    def test_propagates_a_broken_factory(self) -> None:
        """Fail-fast is deliberate here. A factory that raises breaks its contract, so it is an
        adapter bug and the caller should see it. `describe()` is the survivable view."""

        def broken() -> FakeSource:
            raise RuntimeError("credentials file not found")

        registry.register("jobspy", broken)
        with pytest.raises(RuntimeError, match="credentials"):
            registry.create_all()


class TestDescribe:
    def test_is_empty_when_nothing_is_registered(self) -> None:
        assert registry.describe() == []

    def test_reports_each_board_and_whether_it_can_run(self) -> None:
        registry.register("himalayas", lambda: FakeSource("himalayas", available=True))
        registry.register("jobspy", lambda: FakeSource("jobspy", available=False))

        statuses = {s.name: s for s in registry.describe()}
        assert statuses["himalayas"].available is True
        assert statuses["jobspy"].available is False
        assert statuses["jobspy"].error == ""

    def test_a_broken_factory_does_not_hide_the_healthy_boards(self) -> None:
        """The regression: `sources` is the command you run BECAUSE something is broken, so one
        bad adapter blinding you to the other five defeats its purpose."""

        def broken() -> FakeSource:
            raise RuntimeError("credentials file not found")

        registry.register("himalayas", lambda: FakeSource("himalayas"))
        registry.register("jobspy", broken)

        statuses = {s.name: s for s in registry.describe()}
        assert statuses["himalayas"].available is True
        assert statuses["jobspy"].available is False
        assert "credentials file not found" in statuses["jobspy"].error

    def test_a_broken_is_available_is_reported_not_raised(self) -> None:
        """`is_available` is contracted not to raise, but a contract is not an enforcement."""

        class Exploding:
            @property
            def name(self) -> str:
                return "boom"

            def is_available(self) -> bool:
                raise OSError("network unreachable")

            def fetch(self, query: SearchQuery, /) -> SourceResult:
                return SourceResult(source="boom")

        registry.register("boom", Exploding)
        status = registry.describe()[0]
        assert status.available is False
        assert "network unreachable" in status.error

    def test_the_error_names_the_exception_type(self) -> None:
        def broken() -> FakeSource:
            raise RuntimeError("boom")

        registry.register("x", broken)
        assert registry.describe()[0].error.startswith("RuntimeError:")


class TestIsolation:
    def test_each_test_starts_from_an_empty_registry(self) -> None:
        """Pins the fixture itself. Without it, the first real adapter registering at import
        time breaks every assertion in this file."""
        assert registry.names() == []

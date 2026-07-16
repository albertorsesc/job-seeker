"""The hexagon, enforced.

Layer rules are worthless as prose: they hold until the first hurried afternoon. These tests
read every module's imports out of the AST and fail when the arrows stop pointing inward.

This file covers the whole package rather than one module, which is why it sits at the root of
`tests/` instead of in the mirrored tree.

**Known limits.** AST analysis sees names, so `importlib.import_module("job_seeker.infra...")`
and `__import__` escape it. That is an accepted gap: a layer violation smuggled through a
dynamic import is not something a hurried afternoon produces by accident, and the forms that
*are* produced by accident are all covered (see `TestTheDetectorDetects`).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import job_seeker

PACKAGE = "job_seeker"
PACKAGE_ROOT = Path(job_seeker.__file__).parent
REPO_ROOT = PACKAGE_ROOT.parents[1]  # src/job_seeker -> src -> repo root

# Which sibling layers each layer may depend on. Dependencies point inward only.
ALLOWED_INTERNAL: dict[str, set[str]] = {
    "domain": set(),  # the centre depends on nothing of ours
    "application": {"domain"},
    "infrastructure": {"domain", "application"},
}

# Dotted prefixes meaning "this module talks to the outside world". The domain and the
# application reach providers through a port, never by importing the mechanism.
# `urllib.request` rather than `urllib`: `urllib.parse` is pure string work and legitimate.
IO_MODULES = (
    "httpx",
    "requests",
    "urllib.request",
    "http.client",
    "bs4",
    "lxml",
    "yaml",
    "mcp",
    "jobspy",
    "socket",
)


def _package_parts(path: Path) -> tuple[str, ...]:
    """The dotted package a file lives in, e.g. ("job_seeker", "domain"). () if outside the repo."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return ()
    parts = rel.parts[:-1]
    return parts[1:] if parts and parts[0] == "src" else parts


def _imports_from_source(source: str, package_parts: tuple[str, ...]) -> list[str]:
    """Every module a source file imports, as absolute dotted names.

    `from X import Y` yields both `X` and `X.Y`, because Y is frequently a *module*: it is
    `from job_seeker import infrastructure` that a tired person writes, not
    `import job_seeker.infrastructure.sources`. Recording only the module half is how a layer
    violation walks straight past the guard.
    """
    names: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative: resolve against the file's own package
                anchor = package_parts[: len(package_parts) - node.level + 1]
                base = ".".join((*anchor, node.module or "")).rstrip(".")
            else:
                base = node.module or ""
            if not base:
                continue
            names.append(base)
            names.extend(f"{base}.{alias.name}" for alias in node.names)
    return names


def _imports(path: Path) -> list[str]:
    return _imports_from_source(path.read_text(), _package_parts(path))


def _internal_layers_used(names: list[str]) -> set[str]:
    layers: set[str] = set()
    for name in names:
        parts = name.split(".")
        if parts[0] == PACKAGE and len(parts) > 1 and parts[1] in ALLOWED_INTERNAL:
            layers.add(parts[1])
    return layers


def _io_leaks(names: list[str]) -> set[str]:
    return {m for name in names for m in IO_MODULES if name == m or name.startswith(f"{m}.")}


def _layer_of(path: Path) -> str | None:
    rel = path.relative_to(PACKAGE_ROOT)
    return rel.parts[0] if len(rel.parts) > 1 else None


def _module_id(path: Path) -> str:
    return str(path.relative_to(PACKAGE_ROOT))


LAYERED_FILES = [p for p in sorted(PACKAGE_ROOT.rglob("*.py")) if _layer_of(p) in ALLOWED_INTERNAL]
CORE_FILES = [p for p in LAYERED_FILES if _layer_of(p) in {"domain", "application"}]


class TestDependenciesPointInward:
    @pytest.mark.parametrize("path", LAYERED_FILES, ids=_module_id)
    def test_module_imports_only_layers_it_is_allowed_to(self, path: Path) -> None:
        layer = _layer_of(path)
        assert layer is not None
        allowed = ALLOWED_INTERNAL[layer] | {layer}
        illegal = _internal_layers_used(_imports(path)) - allowed
        assert not illegal, (
            f"{_module_id(path)} is in the '{layer}' layer and imports {sorted(illegal)}. "
            f"'{layer}' may only import {sorted(allowed)}."
        )

    def test_domain_imports_nothing_of_ours_outside_itself(self) -> None:
        """The centre of the hexagon. If this fails the layout is decorative."""
        for path in LAYERED_FILES:
            if _layer_of(path) == "domain":
                assert _internal_layers_used(_imports(path)) <= {"domain"}, _module_id(path)


class TestCoreDoesNotTouchTheOutsideWorld:
    @pytest.mark.parametrize("path", CORE_FILES, ids=_module_id)
    def test_no_io_library_in_domain_or_application(self, path: Path) -> None:
        leaked = _io_leaks(_imports(path))
        assert not leaked, (
            f"{_module_id(path)} imports {sorted(leaked)}. Providers reach the core through a "
            f"port in application/ports.py, never by importing the mechanism."
        )


class TestTestsRespectTheSameBoundaries:
    """`tests/` mirrors `src/`, so it inherits the layer rules.

    A test for the core that reaches for a real adapter is evidence the port is not carrying
    its weight. Infrastructure's own tests may of course import infrastructure.
    """

    @pytest.mark.parametrize("layer", ["domain", "application"])
    def test_core_tests_do_not_import_infrastructure(self, layer: str) -> None:
        mirrored = Path(__file__).parent / layer
        if not mirrored.exists():
            pytest.skip(f"tests/{layer}/ does not exist yet")
        # conftest included on purpose: a shared fixture is the likeliest place to reach for
        # a real adapter, and it is the one file every test in the package inherits.
        for path in sorted([*mirrored.rglob("test_*.py"), *mirrored.rglob("conftest.py")]):
            leaked = [n for n in _imports(path) if n.startswith(f"{PACKAGE}.infrastructure")]
            assert not leaked, (
                f"tests/{layer}/{path.name} imports {leaked}. A {layer} test needing a real "
                f"adapter means the port is not doing its job; use a fake that satisfies it."
            )

    def test_the_shared_conftest_does_not_import_infrastructure(self) -> None:
        conftest = Path(__file__).parent / "conftest.py"
        leaked = [n for n in _imports(conftest) if n.startswith(f"{PACKAGE}.infrastructure")]
        assert not leaked, f"tests/conftest.py imports {leaked}; every test would inherit it."


class TestTheDetectorDetects:
    """A guard that cannot fail is decoration.

    These assert on the real output of `_imports_from_source`, not on CPython's ast module.
    Every case below was a live escape at some point; `from job_seeker import infrastructure`
    passed the whole suite until it was pinned here.
    """

    DOMAIN = ("job_seeker", "domain")

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("from job_seeker.infrastructure.sources import Himalayas", id="deep-from"),
            pytest.param("import job_seeker.infrastructure.sources", id="plain-import"),
            pytest.param("from job_seeker import infrastructure", id="from-package-import-layer"),
            pytest.param("from .. import infrastructure", id="relative-import-layer"),
            pytest.param("from ..infrastructure import sources", id="relative-deep"),
            pytest.param("from ..infrastructure.sources import X", id="relative-deeper"),
            pytest.param(
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                "    from job_seeker import infrastructure",
                id="type-checking-block",
            ),
            pytest.param(
                "def f():\n    from job_seeker import infrastructure\n    return infrastructure",
                id="import-inside-function",
            ),
        ],
    )
    def test_catches_every_way_a_domain_module_can_reach_infrastructure(self, source: str) -> None:
        assert _internal_layers_used(_imports_from_source(source, self.DOMAIN)) == {
            "infrastructure"
        }

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("import httpx", id="plain"),
            pytest.param("from yaml import safe_load", id="from"),
            pytest.param("from bs4 import BeautifulSoup", id="parser"),
            pytest.param("import urllib.request", id="urllib-request"),
        ],
    )
    def test_catches_a_leaked_io_library(self, source: str) -> None:
        assert _io_leaks(_imports_from_source(source, self.DOMAIN))

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("from urllib.parse import urlparse", id="urllib-parse-is-pure"),
            pytest.param("import hashlib", id="stdlib"),
            pytest.param("from pydantic import BaseModel", id="pydantic-is-settled"),
            pytest.param("from job_seeker.domain.models import Job", id="own-layer"),
        ],
    )
    def test_does_not_cry_wolf_on_legitimate_imports(self, source: str) -> None:
        names = _imports_from_source(source, self.DOMAIN)
        assert not _io_leaks(names)
        assert _internal_layers_used(names) <= {"domain"}

    def test_application_may_import_domain_but_not_infrastructure(self) -> None:
        app = ("job_seeker", "application")
        assert _internal_layers_used(
            _imports_from_source("from job_seeker.domain.models import Job", app)
        ) == {"domain"}
        assert _internal_layers_used(
            _imports_from_source("from .. import infrastructure", app)
        ) == {"infrastructure"}

    def test_survives_a_file_outside_the_repo(self, tmp_path: Path) -> None:
        """The helper is called on tmp_path files; it must report, not raise."""
        stray = tmp_path / "x.py"
        stray.write_text("from . import helpers\n")
        assert _imports(stray) == [] or isinstance(_imports(stray), list)


class TestEveryLayerDeclaresItsContract:
    @pytest.mark.parametrize("layer", sorted(ALLOWED_INTERNAL))
    def test_layer_package_documents_what_may_live_in_it(self, layer: str) -> None:
        """A new contributor's first question is "where does this go?". Answer it in place."""
        init = PACKAGE_ROOT / layer / "__init__.py"
        assert init.exists(), f"{layer}/__init__.py is missing"
        doc = ast.get_docstring(ast.parse(init.read_text()))
        assert doc, f"{layer}/__init__.py has no docstring stating the layer's contract"

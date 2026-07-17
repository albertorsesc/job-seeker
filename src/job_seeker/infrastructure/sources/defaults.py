"""Registration of the adapters that ship with job-seeker.

Explicit, called once by the composition root (`entrypoints`), rather than each adapter
self-registering when its module is imported. Import-time side effects make registration depend
on which modules happen to have been imported, which is invisible and order-dependent; a single
call in one known place is something a reader can find and a test can control.
"""

from __future__ import annotations

from job_seeker.infrastructure.sources import registry
from job_seeker.infrastructure.sources.himalayas import HimalayasSource
from job_seeker.infrastructure.sources.remoteok import RemoteOkSource

# Name -> factory for every built-in board. Adding a board is one line here plus its adapter.
# Typed as SourceFactory, so mypy checks each entry satisfies the JobSource port with no runtime
# construction: the values stay lazy factories, built only when a query actually uses them.
_BUILTINS: dict[str, registry.SourceFactory] = {
    "himalayas": HimalayasSource,
    "remoteok": RemoteOkSource,
}


def register_builtins() -> None:
    """Register every built-in adapter that is not already registered.

    Skips names already present rather than raising, so calling it from both the CLI and the MCP
    entrypoint (separate processes, but also safe within one) is harmless. The registry's own
    duplicate-name guard still catches the real mistake: two different adapters claiming one name.
    """
    known = set(registry.names())
    for name, factory in _BUILTINS.items():
        if name not in known:
            registry.register(name, factory)

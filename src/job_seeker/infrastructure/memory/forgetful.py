"""A memory that remembers nothing, on purpose.

Substituted when the seeker passes `--no-memory`. A null object rather than an optional dependency
threaded through the pipeline: the orchestrator asks the same questions either way, and a `None`
check at every call site is how "did we mean off, or broken?" stops being answerable.

That distinction is the whole reason this reports `enabled=False` rather than simply being
unavailable. A search that could not read its journal must warn the seeker that their dismissals
are not being honoured. A search the seeker turned off must not.
"""

from __future__ import annotations

from job_seeker.domain.memory import MemoryWrite, PostingDecision, Recollection, Sighting


class ForgetfulMemory:
    """Answers every question with "nothing", and writes nothing anywhere."""

    def recall(self) -> Recollection:
        """Off by choice, so not available and not an error."""
        return Recollection(available=False, enabled=False)

    def record(self, sightings: tuple[Sighting, ...], /) -> MemoryWrite:
        return MemoryWrite()

    def decide(
        self, refs: tuple[str, ...], decision: PostingDecision | None, note: str, /
    ) -> MemoryWrite:
        return MemoryWrite()

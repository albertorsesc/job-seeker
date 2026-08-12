"""The read loop every non-paginating board runs.

Three adapters had hand-copied the same seven lines: count what you looked at, normalize it, drop
what is unusable or too old, stop at the caller's depth. Nothing in that is board knowledge, and
nothing in the suite checked it, so an adapter that quietly forgot to count `scanned`, or that
stopped early while reporting a complete scan, passed every test in the project.

That last one is the expensive mistake, which is why this returns `bounded` rather than leaving
each adapter to work it out. `SearchResult.fully_scanned` is derived from `truncated`, and an agent
is told a partial run was complete. A board reporting that wrongly is a lie the seeker has no way
to detect.

Himalayas is deliberately not a caller. It pages, so its truncation depends on where in a page it
stopped and on its own scan ceiling, and forcing it through this would mean a flag argument that
means "actually do something else". It keeps its own loop and its own tests.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import NamedTuple, TypeVar

from job_seeker.domain.models import Job, SearchQuery
from job_seeker.infrastructure.sources import base

# The board's own record shape: a dict for a JSON board, a Tag for an RSS one. This loop never
# looks inside it; only `normalize` and `skip` do.
Record = TypeVar("Record")


class Scan(NamedTuple):
    """What one read of a board produced.

    `bounded` means the loop stopped with records left unread, so the board genuinely had more to
    give. Not merely "reached the depth": hitting the limit on the very last record leaves nothing
    behind, and reporting that as truncated would warn about a completeness the run actually had.

    It is not the whole of `truncated` either. A board that only ever publishes a window of a much
    larger corpus is truncated whatever this says. It is the part an adapter cannot get wrong by
    accident.
    """

    jobs: list[Job]
    scanned: int
    bounded: bool


def collect(
    records: Iterable[Record],
    normalize: Callable[[Record], Job | None],
    query: SearchQuery,
    /,
    *,
    skip: Callable[[Record], bool] | None = None,
) -> Scan:
    """Read a board's records into canonical jobs, honouring the query.

    `scanned` counts every record examined, including the ones dropped, because coverage reports
    what was read rather than what survived. `skip` is for a board-specific reason to drop a record
    that only makes sense after normalizing succeeded, such as WeWorkRemotely's own expiry date.
    """
    cutoff = base.age_cutoff(query.max_age_days)
    jobs: list[Job] = []
    scanned = 0
    remaining = iter(records)
    for record in remaining:
        scanned += 1
        job = normalize(record)
        if job is None or base.is_stale(job.posted_at, cutoff):
            continue
        if skip is not None and skip(record):
            continue
        jobs.append(job)
        if len(jobs) >= query.scan_depth_per_source:
            # Whether anything is actually left, rather than whether the limit was reached. The
            # iterator is the honest way to ask: it works for a list and for a generator, and it
            # costs reading one more record on the run that stopped early.
            left = next(remaining, None) is not None
            return Scan(jobs=jobs, scanned=scanned + int(left), bounded=left)
    return Scan(jobs=jobs, scanned=scanned, bounded=False)

"""Render a run as JSON.

Presentation only: it serializes the ranked result the domain produced and changes nothing about
which jobs are in it or their order. The structure is the domain models' own, including the
computed `is_complete` and `failed` fields, so a consumer sees the same coverage honesty the
engine computed.
"""

from __future__ import annotations

from job_seeker.domain.models import SearchResult


class JsonReporter:
    """Serializes a SearchResult to indented JSON."""

    def render(self, result: SearchResult, /) -> str:
        return result.model_dump_json(indent=2)

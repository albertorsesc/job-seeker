"""Translate a rejected query into the words the caller actually used.

`SearchQuery` owns the bounds on `max_results_per_source` and `max_age_days`, because the CLI and
the MCP tool both build one and must not disagree about what is acceptable. Neither caller writes
those field names, though: a seeker types `--limit`, and an agent calling `find_jobs` passes
`limit`. A rejection quoting the model's own field name is unactionable at both surfaces, and it
names a parameter that does not exist in either interface.

The mechanism lives here once; the naming is a parameter, because the two surfaces spell the same
argument differently and that difference is exactly what has to be preserved. Adding a third
driving adapter means supplying a third mapping, not writing this again.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError


def describe_bounds_error(exc: ValidationError, names: Mapping[str, str]) -> str:
    """One line per offending field, named as `names` spells it for this surface.

    pydantic's own explanation is quoted rather than reworded, so the acceptable range is stated
    once, in `SearchQuery`. A bound changed there keeps telling the truth here with no edit.

    A field missing from `names` falls back to its own name. That is a worse message, but it is a
    message: silently dropping an error the model raised would leave the caller with an empty
    complaint and no idea what was refused.
    """
    return "\n".join(
        f"{names.get(field, field)}: {error['msg']}"
        for error in exc.errors()
        for field in [str(error["loc"][0]) if error["loc"] else ""]
    )

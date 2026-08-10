"""Covers `job_seeker.infrastructure.entrypoints.bounds`.

`SearchQuery` owns the bounds so the CLI and the MCP tool cannot disagree about what is
acceptable. Neither caller ever writes the model's field names, though, so the rejection has to be
translated into the name that caller used. The translation is shared; the naming is per surface,
because the two surfaces spell the same parameter differently.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_seeker.domain.models import SearchQuery
from job_seeker.infrastructure.entrypoints.bounds import describe_bounds_error


def _rejection(**kwargs: object) -> ValidationError:
    with pytest.raises(ValidationError) as caught:
        SearchQuery(**kwargs)  # type: ignore[arg-type]
    return caught.value


class TestDescribeBoundsError:
    def test_it_names_the_parameter_the_caller_used(self) -> None:
        message = describe_bounds_error(
            _rejection(max_results_per_source=0), {"max_results_per_source": "--limit"}
        )
        assert message.startswith("--limit:")
        assert "max_results_per_source" not in message

    def test_the_same_rejection_renders_per_surface(self) -> None:
        """The whole reason the mapping is a parameter: one model, two spellings."""
        rejection = _rejection(max_results_per_source=0)
        cli = describe_bounds_error(rejection, {"max_results_per_source": "--limit"})
        mcp = describe_bounds_error(rejection, {"max_results_per_source": "limit"})
        assert cli.startswith("--limit:")
        assert mcp.startswith("limit:")

    def test_it_quotes_pydantic_rather_than_restating_the_bound(self) -> None:
        """The acceptable range is stated once, in SearchQuery. Restating it here would mean a
        bound changed there silently starts lying here."""
        message = describe_bounds_error(
            _rejection(max_results_per_source=99999), {"max_results_per_source": "--limit"}
        )
        assert "1000" in message

    def test_every_offending_field_is_reported(self) -> None:
        message = describe_bounds_error(
            _rejection(max_results_per_source=0, max_age_days=0),
            {"max_results_per_source": "--limit", "max_age_days": "--max-age-days"},
        )
        assert "--limit" in message
        assert "--max-age-days" in message
        assert len(message.splitlines()) == 2

    def test_an_unmapped_field_falls_back_to_its_own_name(self) -> None:
        """Better a field name the caller has to look up than a silently dropped error."""
        message = describe_bounds_error(_rejection(max_results_per_source=0), {})
        assert "max_results_per_source" in message

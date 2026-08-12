"""Covers `job_seeker.infrastructure.sources.salary`.

The helper every board hands its untrusted numbers to. It exists because handing them straight to
the model put a `ValidationError` on the normalize path, and one posting with a negative salary
ended an entire board's feed.
"""

from __future__ import annotations

import pytest

from job_seeker.domain.models import CurrencySource, SalaryPeriod
from job_seeker.infrastructure.sources.salary import salary_from_bounds


class TestSalaryFromBounds:
    """Turning a board's two numbers into a SalaryRange, without ever raising.

    This lives here rather than in each adapter because it is not board knowledge: every board can
    send a negative, a NaN, or an upper bound below its lower one, and every adapter must survive
    it identically. Two adapters implementing it separately had already produced two different
    number formats.

    The load-bearing property is that it CANNOT raise. `SalaryRange` rejects a negative and an
    inverted range, `fetch` is contracted never to raise, and one bad row must not cost a board.
    """

    def test_ordinary_bounds_become_a_range(self) -> None:
        salary = salary_from_bounds(
            120_000,
            160_000,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum, salary.currency) == (120_000, 160_000, "USD")

    def test_no_figures_at_all_is_no_salary(self) -> None:
        assert (
            salary_from_bounds(
                None,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_zero_means_unspecified_on_every_board(self) -> None:
        """Both boards document 0 as "unspecified". Expressing that once stops the two adapters
        spelling it differently, which they did: one used truthiness, one a `> 0` predicate."""
        assert (
            salary_from_bounds(
                0,
                0,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )
        floor = salary_from_bounds(
            120_000,
            0,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert floor is not None and (floor.minimum, floor.maximum) == (120_000, None)

    @pytest.mark.parametrize(
        "value",
        [-1, -0.5, float("nan"), float("inf"), float("-inf"), "120k", True, None, [1]],
    )
    def test_an_unusable_figure_is_dropped_rather_than_raised_on(self, value: object) -> None:
        """The regression this exists to prevent: a negative or non-finite figure reached
        `SalaryRange`, whose `ge=0` raised ValidationError out of `_normalize`, out of `fetch`, and
        killed the entire board over one row. `inf` was worse, passing `ge=0` as a salary of
        infinity.
        """
        assert (
            salary_from_bounds(
                value,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )
        assert (
            salary_from_bounds(
                None,
                value,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_a_bool_is_not_a_salary(self) -> None:
        """bool subclasses int, so True would otherwise become a salary of 1."""
        assert (
            salary_from_bounds(
                True,
                None,
                currency="USD",
                currency_source=CurrencySource.PUBLISHED,
                period=SalaryPeriod.YEAR,
            )
            is None
        )

    def test_an_inverted_range_is_kept_as_text_not_swapped_and_not_raised_on(self) -> None:
        salary = salary_from_bounds(
            200_000,
            100_000,
            currency="MXN",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (None, None)
        assert salary.currency == "MXN"
        assert "inverted range" in salary.note
        assert "withheld" in salary.note
        assert "200,000 to 100,000" in salary.note  # stated, not restated as a range

    def test_a_seven_figure_inverted_range_stays_readable(self) -> None:
        """The `,g` format spec used before this was extracted drops to scientific notation past
        six significant digits, so an inverted MXN range rendered as "1.5e+06 - 1e+06"."""
        salary = salary_from_bounds(
            1_500_000,
            1_000_000,
            currency="MXN",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert "1,500,000 to 1,000,000" in salary.note  # not "1.5e+06"

    def test_equal_bounds_are_a_fixed_rate_not_an_inversion(self) -> None:
        salary = salary_from_bounds(
            150_000,
            150_000,
            currency="USD",
            currency_source=CurrencySource.PUBLISHED,
            period=SalaryPeriod.YEAR,
        )
        assert salary is not None
        assert (salary.minimum, salary.maximum) == (150_000, 150_000)

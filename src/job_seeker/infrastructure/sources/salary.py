"""Turning a board's pay figures into a `SalaryRange` the domain will accept.

Split out of `base` because it is the one part of that module that imports our own domain types. A
board publishing no pay at all, like WeWorkRemotely, otherwise sat downstream of the salary model
and recompiled whenever it changed. What is left in `base` is the wire and how to read what comes
off it, which is one nameable reason to change.

Nothing here is board knowledge. Any board can send a negative, a NaN, or an upper bound below its
lower one, and every adapter needs the same answer.
"""

from __future__ import annotations

import math
from typing import Any

from job_seeker.domain.models import CurrencySource, SalaryPeriod, SalaryRange


def salary_from_bounds(
    minimum: Any,
    maximum: Any,
    *,
    currency: str | None,
    currency_source: CurrencySource | None,
    period: SalaryPeriod | None,
    note: str = "",
) -> SalaryRange | None:
    """A board's two pay figures as a `SalaryRange`, or None when it published none usable.

    **Never raises.** That is the whole reason this is a shared helper rather than a line in each
    adapter. `SalaryRange` refuses a negative and refuses an inverted range, `fetch` is contracted
    never to raise, and `_normalize` must not lose a row let alone a board. Handing an adapter's
    untrusted numbers straight to the model put a `ValidationError` on that path: one posting with
    a negative salary ended the entire Himalayas feed.

    Shared because none of this is board knowledge. Any board can send a negative, a NaN, or an
    upper bound below its lower one.

    The currency, where it came from, and the period ARE board knowledge, so all three are
    required keywords with no default. A bare number cannot tell you what it is denominated in,
    whether that unit was stated or inferred, or what it is quoted per, and a default would let a
    new adapter skip the question and publish figures nobody can compare. `None` is a legitimate
    answer for the currency and the period, but it has to be given deliberately: it means "this
    board does not say and I could not establish it", and it makes the annualized figures None
    rather than wrong.
    """
    low, high = _figure(minimum), _figure(maximum)
    if low is None and high is None:
        return None
    if low is not None and high is not None and high < low:
        # The board contradicted itself. Swapping the bounds would report a fact it never
        # published, and dropping them silently would hide that the posting mentioned pay at all.
        #
        # The note says what happened rather than restating the pair as "200,000 - 100,000", which
        # reads as an ordinary range: a reader, and especially an agent, would parse it straight
        # back into the two figures this branch exists to withhold.
        withheld = (
            f"board published an inverted range "
            f"({_figure_text(low)} to {_figure_text(high)}); figures withheld"
        )
        return SalaryRange(
            currency=currency,
            currency_source=currency_source,
            # Both, when the board also published prose: its own words are the source, and the
            # explanation says why no figures came with them.
            note=f"{note} ({withheld})" if note else withheld,
        )
    return SalaryRange(
        minimum=low,
        maximum=high,
        currency=currency,
        currency_source=currency_source,
        period=period,
        note=note,
    )


def _figure_text(value: float) -> str:
    """A figure grouped for reading, with cents only when there are cents.

    Not `:,g`, which caps at six significant digits and flips to scientific notation past it, so a
    seven-figure salary in MXN or JPY rendered as "1.5e+06".
    """
    return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}"


def _figure(value: Any) -> float | None:
    """One pay figure a `SalaryRange` will accept, or None.

    Rejects everything the model would raise on and one thing it would not: `inf` satisfies
    `ge=0`, so an infinite salary would sail through and be reported as a real figure. Zero is
    absent rather than free, which is what both boards mean by it. bool is excluded because it
    subclasses int, so `True` would otherwise be a salary of 1.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        return None
    return number

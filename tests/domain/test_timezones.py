"""Covers `job_seeker.domain.timezones`.

Every band phrase here is a real value captured from the live Remotive API, which is the only
reason the reader is trusted: they are what a board actually writes, not what one might.
"""

from __future__ import annotations

import pytest

from job_seeker.domain.timezones import offsets_for_band


class TestReadingABand:
    @pytest.mark.parametrize(
        "written",
        ["USA timezones", "US timezones", "United States timezones", "usa timezone"],
        ids=["live value", "short name", "full name", "singular"],
    )
    def test_the_us_band_is_the_contiguous_offsets(self, written: str) -> None:
        assert offsets_for_band(written) == (-5.0, -6.0, -7.0, -8.0)

    @pytest.mark.parametrize("written", ["European timezones", "Europe timezones"])
    def test_the_european_band_is_west_central_and_east(self, written: str) -> None:
        assert offsets_for_band(written) == (0.0, 1.0, 2.0)

    def test_a_space_inside_the_word_still_reads(self) -> None:
        assert offsets_for_band("USA time zones") == (-5.0, -6.0, -7.0, -8.0)


class TestWhatIsNotABand:
    @pytest.mark.parametrize("written", ["Germany", "Europe", "Worldwide", "", "   ", "Americas"])
    def test_a_place_is_not_a_band(self, written: str) -> None:
        assert offsets_for_band(written) is None

    def test_a_band_this_map_has_not_been_taught_is_left_alone(self) -> None:
        """None here and None for a plain place, because the caller does the same thing with both:
        keeps them as the restriction they already were, which excludes. An unreadable restriction
        that excludes is recoverable; one that goes quiet promotes a restricted posting."""
        assert offsets_for_band("Asian timezones") is None

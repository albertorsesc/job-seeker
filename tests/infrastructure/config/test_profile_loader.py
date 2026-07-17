"""Covers `job_seeker.infrastructure.config.profile_loader`."""

from __future__ import annotations

from pathlib import Path

import pytest

from job_seeker.infrastructure.config.profile_loader import (
    MarkdownProfileProvider,
    ProfileError,
)

_VALID = """---
name: Jane Doe
headline: Backend Engineer
location:
  country: Portugal
  timezone_utc_offset: 0
eligibility:
  eligible_regions: [portugal, europe]
  max_timezone_distance_hours: 3
skills:
  '\\bpython\\b': 3
search_terms:
  - Backend Engineer
---

# Jane Doe

This prose after the front matter is for humans and must be ignored by the loader.
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "profile.md"
    path.write_text(text)
    return path


class TestLoadingAValidProfile:
    def test_parses_the_front_matter_into_a_profile(self, tmp_path: Path) -> None:
        profile = MarkdownProfileProvider(_write(tmp_path, _VALID)).load()
        assert profile.name == "Jane Doe"
        assert profile.location.country == "Portugal"
        assert profile.eligibility.eligible_regions == ["portugal", "europe"]
        assert profile.search_terms == ["Backend Engineer"]

    def test_the_prose_after_the_front_matter_is_ignored(self, tmp_path: Path) -> None:
        profile = MarkdownProfileProvider(_write(tmp_path, _VALID)).load()
        assert "This prose" not in profile.headline

    def test_terms_are_normalized_by_the_model(self, tmp_path: Path) -> None:
        """The loader hands raw YAML to the model, so the model's validators still run."""
        text = _VALID.replace("[portugal, europe]", "[Portugal, EUROPE]")
        profile = MarkdownProfileProvider(_write(tmp_path, text)).load()
        assert profile.eligibility.eligible_regions == ["portugal", "europe"]


class TestErrorsAreClearAndNameTheProblem:
    def test_a_missing_file_is_a_clear_error(self, tmp_path: Path) -> None:
        provider = MarkdownProfileProvider(tmp_path / "nope.md")
        with pytest.raises(ProfileError, match="not found"):
            provider.load()

    def test_a_file_without_front_matter_is_a_clear_error(self, tmp_path: Path) -> None:
        provider = MarkdownProfileProvider(_write(tmp_path, "# Just prose, no front matter\n"))
        with pytest.raises(ProfileError, match="front matter"):
            provider.load()

    def test_invalid_yaml_names_the_file(self, tmp_path: Path) -> None:
        provider = MarkdownProfileProvider(_write(tmp_path, "---\nname: : :\n---\n"))
        with pytest.raises(ProfileError, match="profile.md"):
            provider.load()

    def test_a_schema_violation_names_the_field(self, tmp_path: Path) -> None:
        bad = "---\nlocation:\n  timezone_utc_offset: not-a-number\n---\n"
        provider = MarkdownProfileProvider(_write(tmp_path, bad))
        with pytest.raises(ProfileError, match="timezone_utc_offset"):
            provider.load()

    def test_an_invalid_skill_regex_is_reported(self, tmp_path: Path) -> None:
        """The model rejects a bad regex; the loader surfaces it as a profile error."""
        bad = "---\nskills:\n  '(python': 3\n---\n"
        provider = MarkdownProfileProvider(_write(tmp_path, bad))
        with pytest.raises(ProfileError):
            provider.load()

    def test_a_non_utf8_profile_is_a_clear_error_not_a_traceback(self, tmp_path: Path) -> None:
        """A profile saved as latin-1 (an accented name) must not decode-crash the CLI."""
        path = tmp_path / "profile.md"
        path.write_bytes("---\nname: Jos\xe9\n---\n".encode("latin-1"))
        with pytest.raises(ProfileError, match="UTF-8"):
            MarkdownProfileProvider(path).load()

    def test_a_bom_prefixed_profile_still_loads(self, tmp_path: Path) -> None:
        """A UTF-8 BOM from a Windows editor must not defeat the leading-fence check."""
        path = tmp_path / "profile.md"
        path.write_bytes(b"\xef\xbb\xbf" + _VALID.encode("utf-8"))
        assert MarkdownProfileProvider(path).load().name == "Jane Doe"


class TestFromEnvironment:
    def test_resolves_the_path_from_the_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = _write(tmp_path, _VALID)
        monkeypatch.setenv("JOB_SEEKER_PROFILE", str(path))
        assert MarkdownProfileProvider.from_env().load().name == "Jane Doe"

    def test_a_missing_env_var_is_a_clear_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("JOB_SEEKER_PROFILE", raising=False)
        with pytest.raises(ProfileError, match="JOB_SEEKER_PROFILE"):
            MarkdownProfileProvider.from_env()

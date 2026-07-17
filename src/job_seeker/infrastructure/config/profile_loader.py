"""Load the seeker profile from a Markdown file with a YAML front-matter block.

A driven adapter behind the `ProfileProvider` port: it is the only place that knows a profile is
Markdown. The domain receives a validated `Profile` and never learns where it came from, so
swapping the format or location touches nothing above this module.

Every failure is a `ProfileError` naming the file and, where possible, the offending field. A
profile is the one input a run cannot sensibly proceed without, so this fails loudly rather than
returning a half-populated object that would produce a confidently meaningless search.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from job_seeker.domain.profile import Profile

_ENV_VAR = "JOB_SEEKER_PROFILE"
_FENCE = "---"


class ProfileError(Exception):
    """The profile could not be read or is not valid."""


class MarkdownProfileProvider:
    """Reads a profile from a Markdown file, validating its front matter into a `Profile`."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @classmethod
    def from_env(cls) -> MarkdownProfileProvider:
        """Build from the `JOB_SEEKER_PROFILE` environment variable."""
        path = os.environ.get(_ENV_VAR)
        if not path:
            raise ProfileError(
                f"No profile configured: set {_ENV_VAR} to your profile file, "
                f"or pass --profile. See examples/profile.example.md for the schema."
            )
        return cls(path)

    def load(self) -> Profile:
        front_matter = self._read_front_matter()
        try:
            return Profile.model_validate(front_matter)
        except ValidationError as exc:
            raise ProfileError(f"{self._path.name} is not a valid profile:\n{exc}") from exc

    def _read_front_matter(self) -> dict[str, Any]:
        try:
            # utf-8-sig strips a BOM if a Windows editor added one, which plain utf-8 would keep
            # and then fail the leading-fence check. A decode failure is a ValueError, not an
            # OSError, so it needs its own handler or an accented profile tracebacks.
            text = self._path.read_text(encoding="utf-8-sig")
        except FileNotFoundError as exc:
            raise ProfileError(f"Profile file not found: {self._path}") from exc
        except UnicodeDecodeError as exc:
            raise ProfileError(
                f"{self._path.name} is not valid UTF-8. Save the profile as UTF-8 ({exc})."
            ) from exc
        except OSError as exc:
            raise ProfileError(f"Could not read profile file {self._path}: {exc}") from exc

        block = _extract_front_matter(text)
        if block is None:
            raise ProfileError(
                f"{self._path.name} has no YAML front matter. The profile must begin with a "
                f"'{_FENCE}' fenced block. See examples/profile.example.md."
            )
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            raise ProfileError(
                f"The front matter in {self._path.name} is not valid YAML:\n{exc}"
            ) from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ProfileError(
                f"The front matter in {self._path.name} must be a mapping of fields, "
                f"not a {type(data).__name__}."
            )
        return data


def _extract_front_matter(text: str) -> str | None:
    """The YAML between the leading `---` fence and the next one, or None if there is no fence.

    The prose after the closing fence is for humans and is dropped here.
    """
    stripped = text.lstrip()
    if not stripped.startswith(_FENCE):
        return None
    body = stripped[len(_FENCE) :]
    end = body.find(f"\n{_FENCE}")
    if end == -1:
        return None
    return body[:end]

"""Where the seeker's posting journal is kept.

The catalogue for this driven package, the same role `reporting/__init__.py` plays for reporters:
one of the three places CLAUDE.md allows to name a concrete adapter. The entrypoints ask for a
memory and never learn it is a file.
"""

from job_seeker.infrastructure.memory.forgetful import ForgetfulMemory
from job_seeker.infrastructure.memory.jsonl import JsonlPostingMemory, default_path

__all__ = ["ForgetfulMemory", "JsonlPostingMemory", "default_path"]

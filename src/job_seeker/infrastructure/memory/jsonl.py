"""A `PostingMemory` over a newline-delimited JSON file on this machine.

JSONL rather than a single JSON document because damage is then line-sized: one unreadable line
costs one posting instead of the whole journal. JSONL rather than SQLite because the write volume
is tens of records a week, because a seeker who regrets a dismissal must be able to fix it in a
text editor, and because the only recovery a non-programmer has when a binary store goes wrong is
deleting all of it.

The file records what the engine **delivered** to the seeker, not what it crawled. A run reads
1,200 postings and returns a few dozen, and only those few dozen are written down, which is what
makes "new" mean new to the person rather than new to the crawler. The visible consequence, worth
knowing before it surprises someone: raising `--max-results` from 5 to 50 announces 45 postings as
new, because the seeker has not been shown them.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from job_seeker.domain.memory import (
    MemoryWrite,
    PostingDecision,
    PostingRecord,
    Recollection,
    Sighting,
    posting_handle,
)

_ENV_VAR = "JOB_SEEKER_STATE"
_DEFAULT_NAME = "postings.jsonl"

# Written on the header line. The store carries its own version so a later build can migrate what
# it finds instead of guessing, and so a build that finds something newer than it understands can
# say so rather than corrupting it.
RECORD_VERSION = 1


class _Line(BaseModel):
    """One journal line as it sits on disk.

    `extra="allow"` inverts this project's `extra="forbid"` convention, deliberately, because the
    author of the data is different. A profile is written by a human, where a typo is the threat
    and forbidding extras catches it. This file is written by another build of this same software,
    where dropping a field you do not recognise is the threat: a newer build's note or decision
    would vanish the first time an older one rewrote the file.
    """

    model_config = ConfigDict(extra="allow")

    kind: str = "posting"
    record_version: int = RECORD_VERSION
    identity_version: int = 1


class JsonlPostingMemory:
    """Reads and writes the seeker's posting journal."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        # Filled by the read that every write does first, and emptied into that write. Instance
        # state rather than a parameter because it is a property of the file, not of the caller.
        self._foreign: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> JsonlPostingMemory:
        """Build from `JOB_SEEKER_STATE`, falling back to the XDG state directory.

        Resolution lives here rather than in the entrypoints so the CLI and the MCP server cannot
        disagree about where the journal is, the same reason `MarkdownProfileProvider.from_env`
        exists.
        """
        return cls(default_path())

    @property
    def path(self) -> Path:
        return self._path

    def recall(self) -> Recollection:
        """Everything remembered. Never raises: a broken journal is a reported fact."""
        if not self._path.exists():
            # The ordinary first run, and the run after a seeker deletes the file. Genuinely empty
            # is available: everything is new because nothing has been shown yet.
            return Recollection(available=True)
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            return Recollection(available=False, error=f"{type(exc).__name__}: {exc}")

        records: dict[str, PostingRecord] = {}
        previous_run_at: datetime | None = None
        for line in text.splitlines():
            parsed = _parse(line)
            if parsed is None:
                continue  # a damaged line costs one posting, not the journal
            if parsed.get("kind") == "header":
                previous_run_at = _read_time(parsed.get("last_run_at"))
                continue
            record = _to_record(parsed)
            if record is not None:
                records[record.key] = record
        return Recollection(records=records, available=True, previous_run_at=previous_run_at)

    def record(self, sightings: tuple[Sighting, ...], /) -> MemoryWrite:
        """Note that these postings were delivered, leaving every decision untouched."""
        if not sightings:
            return MemoryWrite()
        now = datetime.now(UTC)
        try:
            held = self._read_for_write()
        except OSError as exc:
            return MemoryWrite(error=f"{type(exc).__name__}: {exc}")

        for sighting in sightings:
            existing = held.get(sighting.key)
            if existing is None:
                held[sighting.key] = PostingRecord(
                    key=sighting.key,
                    title=sighting.title,
                    company=sighting.company,
                    source=sighting.source,
                    urls=(sighting.url,) if sighting.url else (),
                    first_seen_at=now,
                    last_seen_at=now,
                    times_seen=1,
                )
                continue
            urls = (
                existing.urls if sighting.url in existing.urls else (*existing.urls, sighting.url)
            )
            held[sighting.key] = existing.model_copy(
                update={
                    "last_seen_at": now,
                    "times_seen": existing.times_seen + 1,
                    "urls": tuple(url for url in urls if url),
                }
            )
        return self._write(held, now)

    def decide(
        self, refs: tuple[str, ...], decision: PostingDecision | None, note: str, /
    ) -> MemoryWrite:
        """Set or clear the seeker's decision. All or nothing on unresolved references."""
        if not refs:
            return MemoryWrite()
        now = datetime.now(UTC)
        try:
            held = self._read_for_write()
        except OSError as exc:
            return MemoryWrite(error=f"{type(exc).__name__}: {exc}")

        resolved: dict[str, str] = {}
        unknown: list[str] = []
        by_handle = {posting_handle(key): key for key in held}
        by_url = _urls_claimed_once(held)
        for ref in refs:
            key = ref if ref in held else by_handle.get(ref) or by_url.get(ref)
            if key is None:
                unknown.append(ref)
            else:
                resolved[ref] = key
        if unknown:
            # Nothing is written. A half-applied batch leaves the seeker unable to tell which half
            # landed, and re-running the whole command would then double-count nothing but confuse.
            return MemoryWrite(unknown=tuple(unknown))

        decided: list[PostingRecord] = []
        for key in dict.fromkeys(resolved.values()):
            updated = held[key].model_copy(
                update={
                    "decision": decision,
                    "decided_at": now if decision is not None else None,
                    "note": note if decision is not None else "",
                }
            )
            held[key] = updated
            decided.append(updated)
        written = self._write(held, now, stamp_run=False)
        if written.error:
            return written
        return MemoryWrite(written=len(decided), decided=tuple(decided))

    def _read_for_write(self) -> dict[str, PostingRecord]:
        """Re-read immediately before writing, so a decision made while a search was in flight
        survives that search's write. A run takes seconds; a mark takes one."""
        recollection = self.recall()
        if not recollection.available and recollection.error:
            raise OSError(recollection.error)
        self._foreign = self._read_foreign_fields()
        return dict(recollection.records)

    def _read_foreign_fields(self) -> dict[str, dict[str, Any]]:
        """Fields on disk that this build has no name for, kept aside to be written back.

        The journal is written by other builds of this same software, so a field this one does not
        recognise is a newer version's data, not junk. Dropping it means the first time an older
        build runs, a note or a decision it never heard of is gone, and nothing says so.
        """
        if not self._path.exists():
            return {}
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        known = set(PostingRecord.model_fields) | {"kind", "record_version", "identity_version"}
        foreign: dict[str, dict[str, Any]] = {}
        for line in text.splitlines():
            parsed = _parse(line)
            if parsed is None or parsed.get("kind") == "header":
                continue
            key = parsed.get("key")
            extras = {name: value for name, value in parsed.items() if name not in known}
            if isinstance(key, str) and extras:
                foreign[key] = extras
        return foreign

    def _write(
        self, held: dict[str, PostingRecord], now: datetime, *, stamp_run: bool = True
    ) -> MemoryWrite:
        """Replace the journal atomically.

        Written to a sibling temp file, flushed, fsynced, then moved into place with `os.replace`.
        The fsync is not decoration: without it a power loss can leave a truncated journal on a
        filesystem that does not apply a rename-after-write heuristic, and the seeker's record of
        what they applied to is exactly the thing not to lose.
        """
        header: dict[str, Any] = {
            "kind": "header",
            "record_version": RECORD_VERSION,
            "last_run_at": _write_time(now if stamp_run else self._last_run_at() or now),
        }
        lines = [json.dumps(header, ensure_ascii=False)]
        lines.extend(
            json.dumps(_to_line(record, self._foreign.get(record.key, {})), ensure_ascii=False)
            for record in sorted(held.values(), key=lambda r: r.key)
        )
        body = "\n".join(lines) + "\n"

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle, temporary = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(body)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self._path)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        except OSError as exc:
            return MemoryWrite(error=f"{type(exc).__name__}: {exc}")
        return MemoryWrite(written=len(held))

    def _last_run_at(self) -> datetime | None:
        return self.recall().previous_run_at


def default_path() -> Path:
    """Where the journal lives when nobody said otherwise.

    The XDG state directory, because this is state the seeker would not miss if it vanished but
    would rather keep, which is exactly what that directory is for. Never inside the repo: it
    records which companies someone applied to and when, possibly while employed.
    """
    named = os.environ.get(_ENV_VAR)
    if named:
        return Path(named).expanduser()
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base).expanduser() if base else Path.home() / ".local" / "state"
    return root / "job-seeker" / _DEFAULT_NAME


def _parse(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line:
        return None
    try:
        parsed = json.loads(line)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _to_record(parsed: dict[str, Any]) -> PostingRecord | None:
    try:
        _Line.model_validate(parsed)
        return PostingRecord.model_validate(
            {key: value for key, value in parsed.items() if key in PostingRecord.model_fields}
        )
    except ValidationError:
        return None


def _to_line(record: PostingRecord, foreign: dict[str, Any]) -> dict[str, Any]:
    """One record as a journal line, carrying back any field this build did not understand."""
    payload = record.model_dump(mode="json")
    return {
        "kind": "posting",
        "record_version": RECORD_VERSION,
        "identity_version": 1,
        **payload,
        **foreign,
    }


def _urls_claimed_once(held: dict[str, PostingRecord]) -> dict[str, str]:
    """URLs that identify exactly one posting.

    A URL claimed by two records is dropped rather than pointing at either. Himalayas takes its
    `url` from the board's `applicationLink`, which for some employers is a generic careers page,
    and resolving a reference through one would attach a dismissal to a posting the seeker never
    meant. Ambiguity here has to fail closed: silently hiding a job is the failure that costs them
    one without their ever knowing.
    """
    claims: dict[str, list[str]] = {}
    for key, record in held.items():
        for url in record.urls:
            claims.setdefault(url, []).append(key)
    return {url: keys[0] for url, keys in claims.items() if len(keys) == 1}


def _read_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _write_time(when: datetime) -> str:
    return when.astimezone(UTC).isoformat()

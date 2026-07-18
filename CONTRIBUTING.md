# Contributing to job-seeker

Thanks for helping. This guide gets you set up, explains the one rule that keeps the
architecture honest, and walks through the most common contribution: adding a job board.

## Development setup

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/albertorsesc/job-seeker
cd job-seeker
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,mcp]"
```

Run the gate before every commit. It fixes what a machine can fix, then checks lint, types,
and tests:

```bash
make test
```

That runs `ruff check --fix`, `ruff format`, `mypy --strict`, and `pytest`. CI runs the same
checks without the auto-fix, so if `make test` changed a file, commit that change. Tests never
hit the network (HTTP is mocked with `respx`), so they run offline and fast.

## The one rule: dependencies point inward

The code is a hexagon in three layers, and the direction of imports is enforced by a test
(`tests/test_architecture.py`), not by convention:

- **`domain/`** holds entities, the profile, and the reasoning (scoring, eligibility, relevance,
  dedup). It imports nothing else of ours and no I/O library.
- **`application/`** holds the use case and the ports (Protocols) it needs the outside world to
  satisfy. It imports `domain` only.
- **`infrastructure/`** holds everything that touches the outside world: source adapters,
  reporters, config, and the CLI and MCP entrypoints. It may import both inner layers.

If you add an import that points outward, the architecture test fails and tells you where. That
is the intended feedback, not an obstacle: the fix is almost always to depend on a port instead
of a concrete class.

## Adding a job board

A new source is one adapter file plus one line in the registry. Nothing in the domain, the
application, the pipeline, or the other adapters changes.

### 1. Write the adapter

Create `src/job_seeker/infrastructure/sources/yourboard.py`. It must satisfy the `JobSource`
Protocol from `application/ports.py`, which is three members:

```python
from job_seeker.domain.models import Job, SearchQuery, SourceResult
from job_seeker.infrastructure.sources import base


class YourBoardSource:
    name = "yourboard"  # the stable identifier used to select and report on the source

    def is_available(self) -> bool:
        # Whether the source can run at all. Must not raise and must not do I/O: this is what
        # `job-seeker sources` calls. Return False when an optional dependency or credential is
        # missing (see the JobSpy pattern), True otherwise.
        return True

    def fetch(self, query: SearchQuery, /) -> SourceResult:
        # Fetch and normalize into canonical Jobs. This runs in a worker thread, so it MUST NOT
        # RAISE: a board that is down or returns a surprising shape is an expected outcome, not
        # an exception. Report it as SourceResult(source=self.name, error=...) instead.
        try:
            with base.build_client() as client:
                payload = base.get_json(client, "https://yourboard.example/api")
        except httpx.HTTPError as exc:
            return SourceResult(source=self.name, error=f"{type(exc).__name__}: {exc}")
        jobs = [job for record in payload if (job := _normalize(record)) is not None]
        return SourceResult(source=self.name, jobs=jobs, scanned=len(payload))
```

Use the shared helpers in `sources/base.py`: `build_client`, `get_json` (backs off on HTTP 429
and turns a non-JSON 200 into a reported error), `clean_html`, `to_utc_datetime`, `age_cutoff`,
and `is_stale`. `posted_at` must end up timezone-aware, which `to_utc_datetime` guarantees.

Two adapters show the two shapes a board can take:

- `himalayas.py`: the board publishes **structured** location and timezone restrictions, which
  become `EligibilityHints`. Note `None` (the board said nothing) is different from `()` (the
  board said no restriction), and normalization must preserve that.
- `remoteok.py`: the board publishes **no** structured eligibility data, so its jobs carry hints
  of `None` and the classifier reads the posting text.

Normalization must be defended against untrusted data: a record that is not a dict, a non-numeric
salary, a missing field. Return `None` for an unusable record rather than raising.

### Report coverage honestly (`scanned` and `truncated`)

`SourceResult` carries two coverage fields, and they are part of the answer, not diagnostics. A run
that saw 200 of a board's 100,000 postings and a run that saw all of them are different facts, and
`SearchResult.is_complete` is only as truthful as each adapter's report.

- `scanned`: how many postings this adapter actually examined.
- `truncated`: `True` when the adapter did **not** see the board's whole corpus (it stopped early
  on a scan cap, or the board only ever exposes a slice of itself). `False` only when the adapter
  can honestly say it saw everything the board has.

**Window-only sources (RSS feeds, "latest N" endpoints).** Some boards structurally expose only the
newest slice of a much larger, growing corpus (for example an RSS feed of the latest ~100 of
thousands of postings), with no way to page back further. Such a source can never see the whole
board, so the honest default is **`truncated=True` whenever it returned a full window**: the run
saw a window, not the board. Report `truncated=False` only if the feed genuinely lists the board's
entire corpus (a small board whose feed really is all of it). Decide this once, here, so every
window-only adapter reports it the same way rather than each guessing: a latest-N feed that came
back full is truncated, full stop.

### 2. Register it

Add one line to `src/job_seeker/infrastructure/sources/defaults.py`:

```python
_BUILTINS: dict[str, registry.SourceFactory] = {
    "himalayas": HimalayasSource,
    "remoteok": RemoteOkSource,
    "yourboard": YourBoardSource,  # <- this
}
```

### 3. Test it

Add `tests/infrastructure/sources/test_yourboard.py`, mocking HTTP with `respx` (never the real
network). Mirror the existing source tests: normalization of the core fields, the age and result
budgets, and malformed input. A conformance test parametrized over every registered source
already holds your adapter to the never-raise, no-I/O, name-matches contract, so you get that for
free once it is in `_BUILTINS`.

## Commits and pull requests

- Conventional-commit style, describing the substantive change. No AI attribution or trailers.
- No em dash characters in committed content (this is a public repo); use commas, colons,
  parentheses, or separate sentences. Job-posting text is verbatim data and is exempt.
- Every change ships with its tests, and `make test` is green before you push.

By contributing you agree that your contributions are licensed under the MIT License.

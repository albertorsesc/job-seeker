# job-seeker: Claude Code build guide

This file is the continuation contract for building **job-seeker**, an open-source,
profile-driven job search engine exposed to local AI agents over MCP. A previous session
designed the architecture and started the scaffold. This document lets any fresh Claude Code
session pick up the build without re-deriving decisions.

**Scope note:** this repo will be open-sourced. Everything committed is public. Do not use the
em dash character in committed content (prose, docs, comments, commit messages); use commas,
colons, parentheses, or separate sentences.

---

## 1. What this project is

A single-owner, reusable engine that answers one question well: **"find me the best possible job
I can actually hold."** It aggregates postings from many job boards, normalizes them, scores each
against the owner's profile, classifies whether the owner is eligible to hold it (location, timezone,
work authorization), filters noise, dedupes, ranks, and returns the result to a local agent over MCP
or to a CLI as JSON / CSV / a paginated HTML report.

The engine is **profile-driven**: no candidate specifics are hardcoded. Who the seeker is, where they
live, what they are good at, and where they are legally hireable all come from a profile Markdown file
that lives outside the repo and is located via the `JOB_SEEKER_PROFILE` environment variable. Swap the
profile, and the same code serves anyone. A profile that cannot be swapped is a bug.

It runs on the owner's own machine through a local Claude Code agent over MCP, so a scan never has to
be delegated to a hosted assistant.

---

## 2. Strategies combined (from the best job-search repos)

A synthesis of what the best job-search repos each do well, plus this project's own eligibility
layer. The per-board facts, all captured from live runs, and the steps for adding a board are in
**[docs/sources.md](docs/sources.md)**. They live there rather than here because a contributor
reads `docs/`, and two copies of a board's quirks would drift the week a board changes.

| Source repo | What we take |
|---|---|
| `speedyapply/JobSpy` | Multi-board scraping, wrapped as an optional adapter when built |
| `pranavv00/devjobs.site` | Remote-first aggregation over WWR, Remotive, RemoteOK, WorkingNomads |
| Himalayas public API | Structured `locationRestrictions` and `timezoneRestrictions`, which is what makes precise eligibility possible |
| `santifer/career-ops`, `MadsLorentzen/ai-job-search` | The agent-layer pattern: grade postings, orchestrate as agent tools. We do not install these plugins |
| **Our own contribution** | Profile-driven weighted scoring, the eligibility classifier, noise filtering, dedup, run-to-run memory, multi-format reporting |

## 3. Verified tech choices (do not re-litigate)

- **Language: Python.** All prior implementation (JobSpy, DevJobsHub, our scoring) is Python. Target
  `requires-python = ">=3.11"`. Local dev machine has Python 3.13.7.
- **Version-gated syntax:** target 3.11, so use `typing.Protocol`, `X | Y` unions, `list[str]`. Do
  NOT use PEP 695 `class C[T]` generics (that is 3.12+); use `TypeVar` if generics are needed.
- **MCP: official `mcp` Python SDK, 2.x.** Every line below was verified by introspecting the
  installed package, which is the only source worth trusting here: the 1.x note this replaces was
  itself written after a documentation snippet described an `MCPServer` path that did not exist at
  the time, and 2.0 then made that same name correct. Check the package, not the docs.
  ```python
  from mcp.server.mcpserver import MCPServer
  server = MCPServer("job-seeker", version=__version__, instructions="...")

  @server.tool()
  def find_jobs(...) -> SearchResult: ...

  server.run(transport="stdio")   # Literal["stdio","sse","streamable-http"], default "stdio"
  ```
  `@server.tool()` infers name/description/schema from the function name, docstring and type hints.
  Return pydantic models for structured output.

  What changed from 1.x, and what each fact means for this codebase:
  - `mcp.server.fastmcp.FastMCP` is gone; it is `mcp.server.mcpserver.MCPServer`.
  - `version=` and `instructions=` are constructor arguments. The `_mcp_server` private attribute
    no longer exists, and `server.version` / `server.instructions` are public properties.
  - `call_tool` returns a typed `CallToolResult | InputRequiredResult`, not 1.x's undocumented
    `(content, structured)` tuple. Narrow the union: nothing here elicits, so `InputRequiredResult`
    arriving is a behaviour change worth failing on.
  - **The SDK's own model fields are snake_case now**: `output_schema`, `input_schema`,
    `structured_content`, `is_error`, `server_info`. This is the change most likely to bite,
    because it fails at attribute access rather than at import.
  - **The output schema is still built in validation mode**, so pydantic still omits
    `computed_field` from it. That is why every derived value in the domain is a real field with a
    validator and a `readOnly` marker, and it stays that way. Verified against the live schema.
  - It still accepts an argument it does not know, silently (card 092). 2.0 does not fix that.
- **Package manager:** `uv` (installed at `~/.local/bin/uv`). Build backend: `hatchling`.
- **HTTP:** `httpx` (0.28.1). **HTML/RSS parsing:** `beautifulsoup4` (4.15) + `lxml` (6.1).
  **Validation/models:** `pydantic` v2 (2.13). **Front-matter:** `pyyaml` (6.0). Dates:
  `python-dateutil`.
- **Optional extras** in `pyproject.toml`: `mcp`, and `dev` (`pytest`, `pytest-asyncio`,
  `pytest-cov`, `respx`, `ruff`, `mypy`). There is deliberately no `jobspy` extra until the
  adapter exists: declaring `python-jobspy` for unwritten code pulled in pandas and numpy and
  blocked dependency updates, because it caps `markdownify` below 0.14 and nothing can bump past.

---

## 4. Architecture (hexagonal: ports and adapters)

In **[docs/architecture.md](docs/architecture.md)**, with the layer rules, the SOLID mapping, why
the domain services are not ports, and the pipeline stages. Moved out of this file so a human
contributor finds it: `tests/test_architecture.py` enforces it either way.

## 5. The profile (configuration, not source)

The seeker's real profile is **configuration, not source**, and never lives in this repo. It carries a
real name, location, timezone, weighted skills and work-eligibility rules, so committing one would both
leak personal data and hardcode one candidate into a reusable engine.

Keep it anywhere outside the tree and point the engine at it:

```bash
export JOB_SEEKER_PROFILE=/path/to/your-profile.md
```

It uses the same front-matter schema as `examples/profile.example.md`, which is the only profile the
repo ships and is deliberately fictional. `.gitignore` additionally blocks `profile.md`, `*.profile.md`,
`profiles/`, `.env`, and `*.local.md` as a backstop.

Maintainer-specific setup (real profile path, machine details, local MCP registration) belongs in
`CLAUDE.local.md`, which is gitignored. This file stays generic so it is useful to every contributor.

---

## 6. Conventions

- **SOLID, ports and adapters.** New capability = new adapter behind an existing Protocol. Do not add
  business logic to the MCP or CLI layer; they only translate input/output and call the pipeline.
- **Pydantic v2** for all models. Type hints everywhere; `mypy --strict` must pass.
- **Sync sources, parallel orchestration** (ThreadPoolExecutor). A single source failing logs a warning
  and yields `[]`; it never aborts the run. No silent coverage caps: if a source is bounded or skipped,
  log it.
- **Profile-driven, not hardcoded.** No candidate-specific skills, regions, roles or search terms in
  code, including as a default. A default job title is the same violation as a hardcoded one: it still
  produces plausible results for the wrong person, just silently. Where a rule has no data, empty
  means "rule off"; it never means "match everything", and the engine never invents a value.
- **Tests ship with code**, mirror the source tree (`scoring/eligibility.py` ->
  `tests/scoring/test_eligibility.py`), and never hit the network (`respx` for HTTP). `pytest` green
  before any commit.
- **No em dashes in committed content** (public repo). Job-posting text is verbatim data and is exempt.
- **Attribution:** README + NOTICE credit JobSpy, DevJobsHub, and the Himalayas/Remotive/RemoteOK/WWR
  APIs. MIT license.
- **Commits:** conventional-commit style, substantive change only, no AI attribution/trailers.

---

## 7. How to run and verify (once built)

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,mcp]"

# the gate: ruff --fix, format, mypy strict (src + tests), pytest. Green before any commit.
make test

# real run (hits live boards): write a paginated HTML report
# point this at your own profile, kept outside the repo (see section 5)
export JOB_SEEKER_PROFILE=/path/to/your-profile.md
job-seeker find --limit 50 --format html --out report.html
job-seeker sources         # list available sources and availability

# MCP server (stdio) for a local agent
job-seeker-mcp             # run directly, or register it:
claude mcp add job-seeker -- job-seeker-mcp
# then in a Claude Code session: ask the agent to call find_jobs
```

**Definition of done for v1:** `pytest` green; `ruff`/`mypy` clean; a real `job-seeker find` run
produces a ranked, eligibility-filtered report from at least Himalayas + Remotive + RemoteOK + WWR;
the MCP server starts and `find_jobs` returns structured results to a local agent; README documents
setup; the profile is supplied entirely through `JOB_SEEKER_PROFILE` and the repo contains no
candidate-specific data.


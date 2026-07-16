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

Analyzed from https://github.com/topics/job-search and https://github.com/topics/remote-jobs. This
project is a synthesis of what each does best, plus our own eligibility layer:

| Source repo | What we take |
|---|---|
| `speedyapply/JobSpy` | Multi-board scraping (Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google). Wrapped as an **optional** source adapter (`python-jobspy`), because it is heavy and rate-limit prone. |
| `pranavv00/devjobs.site` (DevJobsHub) | Remote-first aggregation pattern over WeWorkRemotely, Remotive, RemoteOK, WorkingNomads. Their `clean_html` and relative-date parsing ideas are reimplemented in `sources/base.py`. |
| Himalayas public API | The eligibility star: every posting carries structured `locationRestrictions` (list of country/region strings) and `timezoneRestrictions` (list of UTC offsets). This is what makes precise eligibility filtering possible for any seeker location. |
| `santifer/career-ops`, `MadsLorentzen/ai-job-search` (top-starred Claude Code plugins) | The **agent layer** pattern: grade/score postings, orchestrate "find me the best job" as agent tools. We express this as our MCP tools plus profile-driven scoring, but we do NOT install these plugins (they take over an agent's tool loop). |
| **Our own contribution** | Profile-driven weighted scoring, an eligibility classifier (US-only / timezone-lock / region detection), noise filtering (non-engineering titles, human-"agent" false positives), dedup, and multi-format reporting. |

### Verified board facts (captured from live runs; keep in mind when writing adapters)

- **Himalayas**: `GET https://himalayas.app/jobs/api?limit=20&offset=N`. `limit` is capped at 20 per
  page regardless of what you pass. Filter params (`title=`, `search=`, `category=`) are **ignored**;
  the API always returns the full recency-ordered feed (~103k live postings). So you paginate and
  filter client-side. Job fields include `title, excerpt, companyName, minSalary, maxSalary, currency,
  seniority, locationRestrictions (list[str]), timezoneRestrictions (list[float]), categories (list),
  description, pubDate, applicationLink, guid`. A full scan is ~5,155 pages; be polite (~0.15s delay,
  back off on HTTP 429, stop after 3 consecutive empty pages).
- **Remotive**: `GET https://remotive.com/api/remote-jobs?category=software-dev`. Under load it
  throttles and ignores `search=`/`category=` (returns the same ~39). Fields: `title, company_name,
  description, url, publication_date, salary, candidate_required_location, job_type`.
- **RemoteOK**: `GET https://remoteok.com/api`. First array element is legal boilerplate, skip it.
  Filter by `tags`. Default DevJobsHub filter is dev-only; **broaden** to include ai/ml/llm/
  machine-learning/data tags. Fields: `position, company, description, url, date, tags, salary_min,
  salary_max, location`.
- **WeWorkRemotely**: RSS at `https://weworkremotely.com/remote-jobs.rss` (latest ~100). Title is
  `"Company: Role"`, split on first `": "`. Parse with `BeautifulSoup(content, "xml")`.
- **WorkingNomads**: RSS at `https://www.workingnomads.com/jobs/feed/development` (returned 0 in one
  run; treat as best-effort, must not break the pipeline if empty).
- **ZipRecruiter**: blocked by Cloudflare 403 for scrapers; do not rely on it.
- **LinkedIn "Worldwide"** (via JobSpy): returns city-tagged jobs, not hire-from-anywhere. Do not
  treat a LinkedIn worldwide result as globally eligible without reading the posting.

---

## 3. Verified tech choices (do not re-litigate)

- **Language: Python.** All prior implementation (JobSpy, DevJobsHub, our scoring) is Python. Target
  `requires-python = ">=3.11"`. Local dev machine has Python 3.13.7.
- **Version-gated syntax:** target 3.11, so use `typing.Protocol`, `X | Y` unions, `list[str]`. Do
  NOT use PEP 695 `class C[T]` generics (that is 3.12+); use `TypeVar` if generics are needed.
- **MCP: official `mcp` Python SDK, version 1.28.1 installed.** Verified import path empirically
  (a Context7 snippet showed a wrong `MCPServer` path that does not exist in the installed package):
  ```python
  from mcp.server.fastmcp import FastMCP
  mcp = FastMCP("job-seeker")

  @mcp.tool()
  def find_jobs(...) -> dict: ...

  mcp.run(transport="stdio")   # run(transport: Literal["stdio","sse","streamable-http"]="stdio")
  ```
  `@mcp.tool()` infers name/description/schema from the function name, docstring, and type hints.
  Return pydantic models / TypedDict / dict for structured output.
- **Package manager:** `uv` (installed at `~/.local/bin/uv`). Build backend: `hatchling`.
- **HTTP:** `httpx` (0.28.1). **HTML/RSS parsing:** `beautifulsoup4` (4.15) + `lxml` (6.1).
  **Validation/models:** `pydantic` v2 (2.13). **Front-matter:** `pyyaml` (6.0). Dates:
  `python-dateutil`.
- **Optional extras** (already in `pyproject.toml`): `jobspy` (`python-jobspy`), `mcp`, `dev`
  (`pytest`, `pytest-asyncio`, `respx`, `ruff`, `mypy`).

---

## 4. Architecture (clean / layered, SOLID)

Dependencies point inward only. `domain` depends on nothing. Nothing depends on `mcp`/`cli` except
the process entry points.

```
                 mcp/server.py   cli.py            (interface / entry points)
                        \         /
                     pipeline/orchestrator.py       (use-case orchestration)
             /          |           |          |         \
     sources/*    scoring/*    filtering/*   reporting/*   config/*
             \__________\___________|___________/_________/
                              domain/            (models, profile, ports) -- no deps
```

**SOLID mapping (state these in docs/architecture.md):**

- **S (Single responsibility):** each source adapter only fetches+normalizes one board; the scorer
  only scores; the eligibility classifier only classifies; reporters only render.
- **O (Open/closed):** add a new board by writing one `JobSource` and registering it in
  `sources/registry.py`. No change to pipeline, scoring, or reporting.
- **L (Liskov):** every source is substitutable behind `JobSource.fetch(query) -> list[Job]`; a source
  that errors or returns nothing must not break the run (orchestrator isolates failures).
- **I (Interface segregation):** small focused Protocols in `domain/ports.py` (`JobSource`, `Scorer`,
  `EligibilityClassifier`, `RelevanceFilter`, `Deduplicator`, `Reporter`), not one god interface.
- **D (Dependency inversion):** the orchestrator depends on the Protocols, with concrete
  implementations injected (constructor injection + a registry). It never imports a concrete source.

**Concurrency decision:** source `fetch()` is **synchronous** (simple, easy for contributors and
tests). The orchestrator runs sources **in parallel with a `ThreadPoolExecutor`**, so async never
leaks into every layer. MCP tools call the sync pipeline.

**Pipeline stages (orchestrator):** fan out sources concurrently -> collect `Job`s -> dedupe by
`Job.fingerprint` -> score (`FitScore`) -> classify (`Eligibility`) -> filter (relevance + eligibility)
-> rank by fit desc -> return `list[ScoredJob]`.

---

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
uv pip install -e ".[dev,mcp,jobspy]"

# unit tests (must be green, no network)
pytest

# lint + types
ruff check . && mypy

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


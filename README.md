# job-seeker

A profile-driven job search engine. It aggregates postings from many job boards, normalizes them,
scores each against your profile, works out whether you can **actually hold** the role (location,
timezone, work authorization), then filters noise, dedupes, ranks, and reports. It is designed to be
driven by a local AI agent over MCP, or from the command line.

> **Status: early but working.** The engine runs end to end: two boards (Himalayas and RemoteOK),
> the full pipeline (dedupe, score, classify eligibility, filter relevance, rank), a CLI, and an
> MCP server. It is early, so expect rough edges, more boards to come, and no PyPI release yet. See
> [CLAUDE.md](CLAUDE.md) for the architecture and [CONTRIBUTING.md](CONTRIBUTING.md) to add a board.

## Why

Most job boards answer "what is remote?". Almost none answer "what can *I* actually hold?". A posting
tagged remote is routinely US-only in the fine print, or locked to a timezone you cannot work, or
gated behind work authorization you do not have. You find out after reading it.

job-seeker treats eligibility as a first-class, structured filter rather than a keyword, and ranks
what is left by how well it fits you specifically.

## Profile-driven by design

No candidate specifics are hardcoded anywhere in this repo. Who you are, where you live, what you are
good at, and where you are legally hireable all live in a Markdown profile that stays **outside** the
repo. Swap the profile and the same engine serves anyone. A profile that cannot be swapped is a bug.

## Install

Requires Python 3.11 or newer.

> **Not on PyPI:** the name `job-seeker` on PyPI is a different, unrelated project, so
> `pip install job-seeker` does **not** install this tool. Install from this repository instead.

Install from git (works with `pip` or `uv pip`):

```bash
# CLI only
pip install "git+https://github.com/albertorsesc/job-seeker.git"

# CLI plus the MCP server (the optional `mcp` extra)
pip install "job-seeker[mcp] @ git+https://github.com/albertorsesc/job-seeker.git"
```

That gives you the `job-seeker` command (and `job-seeker-mcp` with the extra). To hack on the
project instead, see [CONTRIBUTING.md](CONTRIBUTING.md) for the editable dev setup and `make test`.

## Usage

```bash
export JOB_SEEKER_PROFILE=~/my-profile.md

job-seeker sources                                  # list the boards and whether each can run
job-seeker find --terms "AI Engineer" --format html --out report.html
job-seeker find --format json | jq '.jobs[0]'       # top-ranked job as JSON
```

Each result carries a fit score and an eligibility verdict with a reason, and the report states
per-source coverage, so a partial run (a board down, a scan truncated) is never mistaken for a
thorough one. `find` refuses rather than returning an empty list when it has no way to narrow the
search, because an empty result is indistinguishable from "nothing matched".

The MCP server exposes `find_jobs`, `list_sources`, and `describe_engine` to a local agent:

```bash
claude mcp add job-seeker -- job-seeker-mcp
# then ask the agent: "find me jobs I can hold"
```

## Your profile

The profile is configuration, never source. Copy the template somewhere outside the repo, fill it in,
and point the engine at it:

```bash
cp examples/profile.example.md ~/my-profile.md
$EDITOR ~/my-profile.md
export JOB_SEEKER_PROFILE=~/my-profile.md
```

See [examples/profile.example.md](examples/profile.example.md) for the full schema, and
[.env.example](.env.example) for the other settings.

**Never commit a real profile.** It carries your name, location, and work-eligibility rules.
`.gitignore` blocks `profile.md`, `*.profile.md`, `profiles/`, `.env`, and `*.local.md` as a backstop,
but the rule is simply to keep the file outside the tree.

## Sources

Facts below were captured from live runs. Adding a board means writing one `JobSource` adapter and
registering it, with no change to scoring, filtering, or reporting.

| Source | Status | Access | Notes |
|---|---|---|---|
| Himalayas | built | JSON API | Structured `locationRestrictions` and `timezoneRestrictions` per posting, which is what makes precise eligibility filtering possible. Page size is capped at 20 and filter params are ignored, so pagination plus client-side filtering. |
| RemoteOK | built | JSON API | First array element is legal boilerplate and must be skipped. No structured eligibility data, so its jobs use the text-fallback path. |
| Remotive | planned | JSON API | Throttles under load and then ignores `search`/`category`. |
| WeWorkRemotely | planned | RSS | Latest ~100. Title is `"Company: Role"`. |
| WorkingNomads | planned | RSS | Best effort; has returned empty. Must never break a run. |
| JobSpy | planned | Scraper | Indeed, LinkedIn, Glassdoor, Google. Heavy and rate-limit prone, so an optional extra. |

ZipRecruiter is blocked by Cloudflare for scrapers and is not supported.

## Credits

This project is a synthesis of ideas proven by others:

- [JobSpy](https://github.com/speedyapply/JobSpy) for multi-board scraping, wrapped as an optional source.
- [DevJobsHub](https://github.com/pranavv00/devjobs.site) for the remote-first aggregation pattern.
- The Himalayas, Remotive, RemoteOK, and WeWorkRemotely public APIs and feeds.

See [NOTICE](NOTICE) for attribution details.

## Contributing

Adding a job board is one adapter file plus one line in the registry, and the architecture keeps
you honest with a test. See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup, the layering rule,
and a step-by-step add-a-source guide, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

MIT. See [LICENSE](LICENSE).

# job-seeker

A profile-driven job search engine. It aggregates postings from many job boards, normalizes them,
scores each against your profile, works out whether you can **actually hold** the role (location,
timezone, work authorization), then filters noise, dedupes, ranks, and reports. It is designed to be
driven by a local AI agent over MCP, or from the command line.

> **Status: early development.** The domain model is in place. The source adapters, scoring,
> eligibility classifier, pipeline, reporters, CLI, and MCP server are not built yet, so there is no
> working `job-seeker find` command and nothing is published to PyPI. See [CLAUDE.md](CLAUDE.md) for
> the architecture and the build plan.

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

## Install (development)

Requires Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/albertorsesc/job-seeker
cd job-seeker
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,mcp]"
```

Run the gate:

```bash
pytest          # unit tests, no network
ruff check .    # lint
mypy            # types, strict
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

## Planned sources

Facts below were captured from live runs. Adding a board means writing one `JobSource` adapter and
registering it, with no change to scoring, filtering, or reporting.

| Source | Access | Notes |
|---|---|---|
| Himalayas | JSON API | Structured `locationRestrictions` and `timezoneRestrictions` per posting, which is what makes precise eligibility filtering possible. Page size is capped at 20 and filter params are ignored, so pagination plus client-side filtering. |
| Remotive | JSON API | Throttles under load and then ignores `search`/`category`. |
| RemoteOK | JSON API | First array element is legal boilerplate and must be skipped. Filter by tags. |
| WeWorkRemotely | RSS | Latest ~100. Title is `"Company: Role"`. |
| WorkingNomads | RSS | Best effort; has returned empty. Must never break a run. |
| JobSpy (optional) | Scraper | Indeed, LinkedIn, Glassdoor, Google. Heavy and rate-limit prone, so it is an optional extra. |

ZipRecruiter is blocked by Cloudflare for scrapers and is not supported.

## Credits

This project is a synthesis of ideas proven by others:

- [JobSpy](https://github.com/speedyapply/JobSpy) for multi-board scraping, wrapped as an optional source.
- [DevJobsHub](https://github.com/pranavv00/devjobs.site) for the remote-first aggregation pattern.
- The Himalayas, Remotive, RemoteOK, and WeWorkRemotely public APIs and feeds.

See [NOTICE](NOTICE) for attribution details.

## License

MIT. See [LICENSE](LICENSE).

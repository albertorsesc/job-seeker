# job-seeker

Most job boards answer "what is remote?". Almost none answer "what can *I* actually hold?"

A posting tagged remote is routinely US-only in the fine print, locked to a timezone you cannot
work, or gated behind work authorization you do not have. You find out after reading it.

job-seeker aggregates postings from several boards, works out whether you are eligible to hold each
one, ranks what is left against your profile, and tells you honestly how much it managed to look
at. It runs on your machine, from the command line or through a local AI agent over MCP.

> **Status: early but working.** Three boards (Himalayas, RemoteOK, Remotive), the full pipeline, a CLI, and an
> MCP server. Pre-1.0, so the profile schema and the payload shape can still change. More boards
> are planned. Not on PyPI: see [Install](#install).

## What makes it different

**Eligibility is a structured verdict, not a keyword.** Every posting comes back classified as
`home-based`, `regional`, `global`, `remote-verify`, or excluded for location, timezone or work
authorization, each with a reason in plain words. The rules come from your profile, so the same
engine serves a seeker in Mexico and one in Portugal without a line of code changing.

**Pay is comparable.** Boards quote hourly and annual figures in the same field, so an $85/hour role
sorts below a $60,000 one if you rank on the raw number. Each board adapter declares what its
figures mean, and every posting carries an annualized equivalent alongside what the board actually
published. When a board does not say, the annual figure is `null` rather than guessed.

**A partial run says so.** If a board is down or a scan was capped, that is in the result, not a log
line. An empty answer and a broken answer are different things and the engine never conflates them.

**Nothing about you is in this repo.** Your name, location, skills and work-eligibility rules live
in a Markdown file outside the tree. Swap the profile and the engine serves someone else. A profile
that cannot be swapped is a bug.

## Install

Requires Python 3.11 or newer.

> **Not on PyPI.** The name `job-seeker` there belongs to an unrelated project, so
> `pip install job-seeker` fetches the wrong thing. Install from this repository.

```bash
# CLI only
pip install "git+https://github.com/albertorsesc/job-seeker.git"

# CLI plus the MCP server for a local agent
pip install "job-seeker[mcp] @ git+https://github.com/albertorsesc/job-seeker.git"
```

`uv pip install` works the same way. To hack on the project instead, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

```bash
# 1. Copy the template somewhere outside the repo and fill it in.
cp examples/profile.example.md ~/my-profile.md
$EDITOR ~/my-profile.md
export JOB_SEEKER_PROFILE=~/my-profile.md

# 2. Check the engine can run.
job-seeker sources
#   himalayas       available
#   remoteok        available
#   remotive        available
#   weworkremotely  available

# 3. Search.
job-seeker find --terms "AI Engineer" --max-results 20 --format html --out report.html
```

The profile is the whole configuration. Spend ten minutes on it: everything below is downstream of
what you put there. See [examples/profile.example.md](examples/profile.example.md) for the annotated
schema and [.env.example](.env.example) for the rest.

**Never commit a real profile.** It carries your name, location and work-eligibility rules.
`.gitignore` blocks `profile.md`, `*.profile.md`, `profiles/`, `.env` and `*.local.md` as a
backstop, but the rule is to keep the file outside the tree.

## Using it from the command line

```bash
job-seeker sources                    # which boards exist, and whether each can run right now
job-seeker find --terms "AI Engineer" --format json | jq '.jobs[0]'
job-seeker find --max-results 10 --format html --out report.html
```

| flag | what it does |
|---|---|
| `--terms` | comma-separated search terms. Defaults to your profile's `search_terms` |
| `--scan-depth` | how many postings to **read** per board (default 1000, which is also the most it will read). Lowering it makes a search faster and its answer worse |
| `--max-results` | how many ranked results to **return**. Applied after ranking, so it keeps the best |
| `--max-age-days` | ignore postings older than this (default 30) |
| `--stated-only` | only postings a board affirmatively cleared, dropping `remote-verify` |
| `--sort` | `fit` (default) or `confidence`, which puts everything a board cleared above everything nobody did |
| `--min-fit` | drop postings below this fit, 0.0 to 1.0 (default 0.0, keep everything) |
| `--new-only` | only postings the engine has not shown you before |
| `--include-dismissed` | show postings you dismissed; hidden by default |
| `--no-memory` | read and write nothing this run |
| `--state` | the posting journal to use (env: `JOB_SEEKER_STATE`) |
| `--sources` | restrict to named boards. A typo is refused rather than silently searching fewer |
| `--format` | `html` (default), `json`, or `csv` |
| `--out` | write to a file instead of stdout |

`--scan-depth` and `--max-results` are deliberately separate. Reading more postings costs time and
politeness; returning fewer costs nothing. Asking for a short list by scanning less would hand you
the first few postings found rather than the best ones.

`--min-fit` is the other half of reading deeply. Fit is the share of your **whole** profile a
posting matched, so no real posting scores near 100: on one profile a full-depth run left 50
holdable postings whose best fit was 52% and whose median was 4%. Ranking puts the good ones first,
and a floor removes the tail entirely. Read a few results first, then set the floor from what you
saw, because the right number depends on how broad your profile is.

The depth ships at its maximum because reading shallowly measurably costs the answer. Boards order
their feeds by recency, not by how well a posting suits you, so a shallow scan reads the newest
postings rather than the best ones. On one profile, a depth of 50 read 180 postings in about a
second and the largest board contributed nothing at all; 1000 read 1,220 in about thirteen seconds,
moved the best eligible match from 34% to 52% fit, and put three roles from that board in the top
five. Thirteen seconds is the cheap resource; the pool is the scarce one.

### Running it week after week

The engine remembers which postings it has shown you, so the second run is more useful than the
first. A posting is **new** when this run is the first time the engine has put it in front of you,
which needs no clock and no configuration: it does not mean the board posted it recently.

```bash
job-seeker find --new-only                       # just the delta since last time
job-seeker mark dismissed jk_9f2c1a3b04d7e551    # never show me this again
job-seeker mark applied jk_3c3554201a58a531 --note "referred by K"
job-seeker unmark jk_9f2c1a3b04d7e551            # changed my mind
```

Every posting in the HTML report carries its own `job-seeker mark dismissed ...` line, so the
loop closes from the page you are reading back to the terminal without looking anything up. The
CSV carries `handle`, `new`, `times_seen` and `decision` as four new columns at the end.

A reference is the handle printed with each posting, the raw identity key, or the posting's URL,
so you can paste whichever you already have. Marking is all or nothing: if one reference does not
match, nothing is written and the unmatched ones are listed, because a dismissal landing on the
wrong posting is worse than a typo being reported.

The journal is a plain JSONL file at `$XDG_STATE_HOME/job-seeker/postings.jsonl`, or
`~/.local/state/job-seeker/postings.jsonl`. Never inside this repo: it records which companies you
applied to and when. Back it up with `cp`, edit it in any text editor, and delete it to start over,
which loses your dismissals and nothing else.

Two behaviours worth knowing before they surprise you. The journal holds what the engine
**delivered**, not what it crawled, so raising `--max-results` from 5 to 50 announces 45 postings as
new, correctly, because you have not been shown them. And if the journal cannot be read, the search
still runs: `--new-only` is ignored rather than honoured, your dismissals are not applied, and it
says so on stderr, because an empty list that reads as "nothing new this week" is the one lie that
costs you a job without your ever knowing it was told.

### "US job, but open to remote"

That is not a special case. The engine never asks whose company it is, only what the posting says
about who may hold the role. A US company that opens a role to Mexico or worldwide comes back
`home-based` or `global`; one that does not comes back excluded.

The `eligibility.status` is the confidence signal:

- `home-based`, `regional`, `global` mean **the board stated it**. Apply.
- `remote-verify` means nobody said. A lead worth checking, not a fact.

Measured against 800 live Himalayas postings: 63% are tagged United States only, and 16% of those
actively demand US citizenship, a green card or a clearance. Only 2% contain any "open to anywhere"
phrase, and on inspection those are perks blurbs rather than hiring policy. Postings tagged for the
US plus other countries list Canada, the UK, Australia, India and Germany, and never Mexico or
LATAM. So the tag is trustworthy to exclude on. What you want, roughly 8% of postings, arrives as
`[]` open-to-anyone, an explicit Mexico, a LATAM country, or worldwide, and all of it is surfaced.

Use `--stated-only --sort confidence` when you want only what you can definitely hold.

`find` refuses rather than returning an empty list when it has nothing to narrow by, because an
empty result is indistinguishable from "nothing matched".

### What a result looks like

```json
{
  "job": {
    "title": "Java Developer",
    "company": "Clera",
    "source": "remoteok",
    "url": "https://remoteOK.com/remote-jobs/remote-java-developer-clera-1136188",
    "salary": null
  },
  "fit": {
    "value": 0.4211,
    "raw": 8,
    "matched": { "\\bgo\\b|golang": 3, "kubernetes|k8s": 2, "distributed systems": 3 }
  },
  "relevance": { "keep": true, "reason": "title matches 'developer'" },
  "eligibility": {
    "status": "remote-verify",
    "reason": "remote, but eligibility could not be confirmed",
    "is_eligible": true
  }
}
```

Every stage explains itself. `fit.value` is `0.0-1.0`, the share of your profile's total weight this
posting matched, so it means the same thing across profiles, and `matched` says which of your skill
patterns earned it. `relevance` says why the posting is on topic. `eligibility` says whether you can
hold it and why.

Alongside the jobs, every run reports what it saw:

```json
{
  "coverage": [
    { "source": "himalayas", "scanned": 80, "kept": 1, "truncated": true, "failed": false },
    { "source": "remoteok",  "scanned": 80, "kept": 3, "truncated": true, "failed": false }
  ],
  "all_sources_ran": true,
  "fully_scanned": false
}
```

`all_sources_ran: false` means a board failed and whole categories of job are missing.
`fully_scanned: false` means a scan hit `--scan-depth`, which is the ordinary case. They are separate
because a flag that is false on every run is a flag nobody reads.

## Using it from an AI agent

This is the interface the project is built around: the search runs on your machine, so a scan is
never delegated to a hosted assistant.

```bash
claude mcp add job-seeker -- job-seeker-mcp
```

`JOB_SEEKER_PROFILE` must be visible to the agent's environment. Then talk to the agent normally:

> "Find me jobs I can actually hold, and tell me if anything looks off."

Four tools, and the agent is meant to use them together:

| tool | what it answers |
|---|---|
| `describe_engine` | Is this configured and able to search? Names the problem when it is not |
| `describe_profile` | **Who am I searching as?** Your name, location, skills, and the eligibility rules that decide every verdict |
| `list_sources` | Which boards exist, and can each one run right now |
| `find_jobs` | The search. `terms`, `scan_depth`, `max_results`, `min_fit`, `new_only`, `include_dismissed`, `max_age_days`, `sources` |
| `mark_jobs` | Record that you applied to a posting, or that you want it gone. The agent is told never to infer this |
| `unmark_jobs` | Undo that |

`describe_profile` exists because a misconfigured profile does not error, it answers confidently for
the wrong person. An agent should state whose profile it used before you act on the results.

A capable agent will typically check the engine and the profile, run the search, and then caveat its
answer using `all_sources_ran`. Job descriptions are trimmed in the MCP payload to keep a whole
search inside a context window; the full posting is always one fetch away at `job.url`.

## How it decides

```
fetch boards concurrently
  -> drop anything older than --max-age-days
  -> merge the same posting across boards
  -> drop what you did not search for      (relevance)
  -> score against your profile            (fit)
  -> classify whether you can hold it      (eligibility)
  -> rank by fit, cap to --max-results
```

**Eligibility** takes the precise path when a board publishes structured restrictions, and reads the
posting text against your profile's term lists when it does not. A region in your profile is an
**authorization claim, not geography**: listing `americas` includes the United States, so if you
cannot work there, list `latam` or specific countries. The engine cannot infer work authorization
from a map and will not try.

**Merging** keeps the freshest copy of a posting as the representative and fills anything it lacks
from the other boards' copies, so a barer duplicate never costs you a published salary.

## Sources

Adding a board is one adapter file plus one line in the registry. Nothing in the pipeline changes.

| Source | Status | Access | Notes |
|---|---|---|---|
| Himalayas | built | JSON API | Structured `locationRestrictions` and `timezoneRestrictions` per posting, which is what makes precise eligibility possible. Page size caps at 20 and filter params are ignored, so pagination plus client-side filtering |
| RemoteOK | built | JSON API | First array element is legal boilerplate. No structured eligibility data, so its postings take the text path |
| Remotive | built | JSON API | `candidate_required_location` is a comma-separated list of places and, on some postings, timezone bands like "USA timezones"; each goes to the field that matches what it is. Salary is free text with an explicit period. The API returns exactly 20 postings and ignores every parameter, so a run always reports `truncated` |
| WeWorkRemotely | built | RSS | `country` names the places a posting is open to, so eligibility is precise. Its `region` field is not a restriction: most postings say "Anywhere in the World" and some of those are US-only, so only `country` is reported as one. No pay field. The feed is ten postings per category and does not paginate, so a run always reports `truncated` |
| WorkingNomads | planned | RSS | Best effort; has returned empty. Must never break a run |
| JobSpy | planned | Scraper | Indeed, LinkedIn, Glassdoor, Google. Heavy and rate-limit prone, so an optional extra |

ZipRecruiter is blocked by Cloudflare for scrapers and is not supported.

What each board actually returns, and the steps for adding one, are in
[docs/sources.md](docs/sources.md). The layer rules and why they are shaped that way are in
[docs/architecture.md](docs/architecture.md).

## Limitations worth knowing

- **Three boards today.** Coverage is genuinely partial, and the engine tells you so on every run
  rather than implying otherwise.
- **`remote-verify` means unverified.** When a board publishes no eligibility data and the text says
  nothing conclusive, the posting is shown with that status rather than hidden. Read those before
  applying. Set `include_unverified: false` in your profile to drop them.
- **Pay periods are sometimes unknown.** Himalayas publishes no period field, so its adapter infers
  one from magnitude within bands measured against live data and declines to guess in between. Those
  postings carry a `null` annual figure, and a comparison that ignores them is wrong rather than
  merely incomplete.
- **Currencies are not converted.** A EUR figure and a USD figure are not comparable, and
  `currency_source` tells you whether a board stated the currency or an adapter assumed it.
- **Dedup errs toward keeping too much.** "Senior AI Engineer" and "AI Engineer" stay separate.
  Showing a duplicate is a smaller failure than silently dropping a real role.

## Contributing

Adding a job board is one adapter file plus one registry entry, and the architecture keeps you
honest with a test: `tests/test_architecture.py` reads every module's imports and fails when a
dependency points the wrong way. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the layering
rule, and a step-by-step guide, and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev,mcp]"
make test        # ruff, ruff format, mypy --strict, pytest
```

## Credits

A synthesis of ideas proven by others:

- [JobSpy](https://github.com/speedyapply/JobSpy) for multi-board scraping, planned as an optional source.
- [DevJobsHub](https://github.com/pranavv00/devjobs.site) for the remote-first aggregation pattern.
- The Himalayas, Remotive, RemoteOK and WeWorkRemotely public APIs and feeds.

See [NOTICE](NOTICE) for attribution details.

## License

MIT. See [LICENSE](LICENSE).

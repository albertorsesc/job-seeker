# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Pre-1.0 and not yet depended on, so these breaks are taken now rather than carried.

### Changed

- **Breaking:** the fit score is normalized. `FitScore.value` is a `0.0-1.0` fraction (matched
  weight over the profile's total available weight) instead of a raw integer sum, so it means the
  same thing across profiles. The raw sum moves to `FitScore.raw`, and `matched` becomes a
  `pattern -> weight` map, so reports can explain a score ("python +3, rag +2") rather than state it.
- **Breaking:** the relevance stage records why it kept or dropped a posting instead of returning a
  bare boolean. Every result carries a `relevance` object, and `Eligibility.reason` is now required.
- **Breaking:** pay is structured, comparable, and carries its provenance. `Job.salary` is a
  `SalaryRange` or `null`, replacing a display string each adapter built for itself:

  - `minimum`, `maximum`, `currency` are the board's own figures; `note` is prose for boards that
    publish "Competitive, DOE" instead of a number.
  - `period` (`hour`..`year`, or `null`) says what the figures are quoted per, with derived
    `annual_minimum`/`annual_maximum` for comparison. Boards mix hourly and annual pay freely, so
    ranking on the bare number put an $85/hour role below a $60,000 one. The annual figures assume
    full time and are `null` rather than guessed when the period is unknown.
  - `currency_source` distinguishes a currency the board published from one an adapter asserted;
    they were previously indistinguishable on the wire.

  Each adapter declares its board's currency, that currency's origin, and its period, and
  `base.salary_from_bounds` requires all three, so a new board cannot skip the question. Formatting
  moved from the adapters to the reporters: the CSV gains sortable `salary_min`, `salary_max`,
  `currency`, `currency_source`, `salary_period`, `annual_min`, `annual_max` and `salary_note`
  columns in place of one `salary` string.
- **Breaking:** `--limit` split into `--scan-depth` and `--max-results` (MCP: `scan_depth`,
  `max_results`). One parameter meant both "how deep to read each board" and "how many results to
  return", and it silently meant the first: lowering it shrank the candidate pool *before* ranking,
  so asking for a shorter list returned worse jobs rather than fewer. `--max-results` applies after
  ranking. `coverage.kept` still counts what each board matched, so `sum(kept)` exceeding the
  returned count is how a caller sees the cap bite.
- **Breaking:** `SearchResult.is_complete` split into `all_sources_ran` and `fully_scanned`. One
  boolean covered both a board failing and a scan being capped, and since the default depth always
  caps a ~98,000-posting feed it was false on every run, which is the same as being absent.
- **Breaking:** a board whose adapter cannot be constructed is reported as a failed source in
  `coverage` rather than ending the run. `job-seeker find` used to exit with a traceback while
  `job-seeker sources` reported the same board cleanly.
- The MCP `find_jobs` tool publishes an output schema. It returned `dict[str, Any]`, so `tools/list`
  described nothing and an agent had to infer the payload shape from an example.

### Added

- `job-seeker find` prints a notice to stderr when a run was not exhaustive: a warning naming the
  boards that failed, or a quieter line when a scan was merely capped. JSON and HTML carry coverage
  in the report, but CSV is a flat table of jobs with nowhere to put it, so a failed board there was
  a header row and silence.
- `describe_profile`, an MCP tool reporting who the engine is searching as. Every verdict is a
  function of the profile, and a misconfigured one does not error, it answers confidently for the
  wrong person; an agent can now state whose profile it used before reporting results.
- `describe_engine` checks whether a profile is loadable instead of reporting `can_search: true`
  unconditionally, and returns the reason when it is not.

### Fixed

- The MCP payload no longer floods an agent's context. Job descriptions are trimmed to 600
  characters with the full posting a fetch away at `job.url`. A broad live search cost roughly
  29,000 tokens, 82% of it descriptions, and the SDK sends the payload twice; the same search is
  now about 7,200. Trimming happens at the tool boundary, so scoring and eligibility still read
  the full text.
- An unknown key in a profile is now an error naming the key, instead of being silently ignored.
  A real profile carried `exclude_us_only: true` and two other rules from a superseded schema; all
  three were dropped in silence, the profile reported itself valid, and a seeker who cannot work in
  the United States was shown US-only roles as eligible. A misspelled rule failed the same way.
- Cross-board dedup no longer discards data. The freshest posting is still the representative, but
  a field it lacks is filled from its siblings, so a copy posted an hour later with no salary no
  longer takes a published salary with it.
- Eligibility text path no longer reads a place embedded in a larger one as the seeker's home or
  region: a posting in "New Mexico" is no longer home-based for a Mexico-based seeker.
- A search term whose punctuation carries the meaning is matched whole. `"C++"` was split to `"c"`,
  so a C++ seeker matched C, C# and C++ alike, each reporting `title matches 'c'`.
- An out-of-range argument is reported as a message naming the flag or tool parameter the caller
  actually used, instead of an unhandled pydantic traceback quoting an internal field name.
- A board sending a negative, NaN or infinite salary no longer ends that board's entire fetch.
- `Retry-After` is honored when a board sends it as an HTTP-date, which RFC 9110 permits alongside
  a number of seconds. Only the numeric form was read, so a board asking for a real pause was
  retried on the two-second default.

## [0.1.0] - 2026-07-18

First working release. Pre-1.0, so the API and profile schema may still change in a minor
release.

### Added

- Profile-driven domain model: a Markdown profile with YAML front matter drives scoring,
  eligibility, and relevance; nothing candidate-specific is hardcoded.
- Hexagonal architecture (domain, application, infrastructure) with the dependency direction
  enforced by a test.
- Source adapters behind a `JobSource` port: Himalayas (structured eligibility hints) and RemoteOK
  (text-fallback eligibility), registered through an open/closed registry.
- The combination pipeline: fan out sources concurrently, dedupe the same posting across boards,
  score against the profile, classify eligibility, filter by relevance, and rank by fit.
- A country-to-region map so a profile region (`latam`) accepts a board's country restriction
  (`Brazil`).
- Three-state eligibility hints (unknown / unrestricted / restricted) and honest per-source
  coverage, so a partial run is never mistaken for a complete one.
- `job-seeker` CLI (`find`, `sources`) with JSON, CSV, and self-contained HTML reports.
- MCP server exposing `find_jobs`, `list_sources`, and `describe_engine` to a local agent.

[Unreleased]: https://github.com/albertorsesc/job-seeker/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/albertorsesc/job-seeker/releases/tag/v0.1.0

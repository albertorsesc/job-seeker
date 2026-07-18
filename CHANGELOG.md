# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

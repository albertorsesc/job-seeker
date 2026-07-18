# Security Policy

## Supported versions

job-seeker is pre-1.0 and under active development. Only the latest `main` receives fixes; there
are no maintained release branches yet.

| Version | Supported |
|---|---|
| `main` (latest) | yes |
| older commits | no |

## Reporting a vulnerability

Please do not open a public issue for a security vulnerability.

Report it privately through GitHub's private vulnerability reporting: on the repository's
**Security** tab, choose **Report a vulnerability**. That opens a confidential advisory visible
only to the maintainers.

Include what you would put in a good bug report: what the issue is, how to reproduce it, and the
impact you see. We will acknowledge the report, work on a fix, and credit you in the advisory
unless you prefer to remain anonymous.

## Scope notes

This tool fetches data at runtime from third-party job boards on the user's own machine. A few
things are in scope worth knowing:

- Reports render untrusted board data. The HTML report links a job only when its URL is
  `http(s)`, and the CSV report neutralizes spreadsheet-formula cells, so a hostile posting
  cannot execute code when a report is opened. A gap here is a security issue.
- A source adapter must never let a malformed or hostile response crash a run; that isolation is
  a safety property, and a bypass is in scope.
- The engine runs no code from postings and stores no credentials. A real profile is the user's
  private data and must stay outside the repository.

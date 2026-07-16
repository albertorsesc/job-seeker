---
# job-seeker profile template.
# Copy this file somewhere private (NOT inside the repo), fill it in, and point
# the engine at it with the JOB_SEEKER_PROFILE environment variable or the
# --profile flag. The YAML front matter below is the machine-readable part the
# engine parses; the Markdown prose after it is for humans and is ignored.

name: Jane Doe
headline: Senior Backend Engineer
seniority: senior

location:
  country: Portugal
  # Your working timezone as a UTC offset (e.g. 0 for Lisbon, -6 for Mexico City).
  timezone_utc_offset: 0

eligibility:
  # Region names (lower-cased substrings) that count as "I can work here".
  # A posting whose stated location matches any of these is treated as eligible.
  eligible_regions:
    - portugal
    - europe
    - emea
    - worldwide
    - anywhere
    - global
    - remote
  # Drop postings that require residency / work authorization you do not have.
  exclude_us_only: true
  # Drop postings whose timezone window cannot include your offset.
  exclude_timezone_locked: true
  # Offsets (hours) you can realistically overlap with. Leave empty to accept any.
  acceptable_timezone_offsets: [0, 1, 2]

# Weighted fit signals: regex pattern -> weight. Higher weight = stronger fit.
# Patterns are matched (case-insensitive) against the title + description + location.
# Use \b for word boundaries so "go" does not match "google".
skills:
  '\bpython\b': 3
  '\bgo\b|golang': 3
  'postgres|postgresql': 2
  'kubernetes|k8s': 2
  'microservice|event-driven': 2
  'grpc|graphql': 2
  'aws|gcp|azure': 1
  'terraform|infrastructure as code': 1
  'distributed systems': 3

# A posting title must contain one of these to count as a real IC engineering role.
role_include:
  - engineer
  - developer
  - architect

# Titles containing any of these are never relevant to you.
role_exclude:
  - marketing
  - sales
  - recruiter
  - manager
  - designer

# Titles where a matched keyword names a human role, not the thing you build
# (for example an "AI" seeker filtering out "support agent" jobs).
false_positive_terms:
  - support agent
  - service desk
  - virtual assistant

# Default search terms when the caller supplies none.
search_terms:
  - Backend Engineer
  - Software Engineer
  - Platform Engineer
---

# Jane Doe

Senior Backend Engineer, Lisbon (UTC+0). This prose section is optional and ignored by the
engine. Use it to keep a human-readable summary of your experience next to the machine-readable
front matter that drives scoring and filtering.

## Notes on filling this in

- **skills**: start from the tools and concepts you want to be hired for, and weight the ones
  that matter most higher. The weights only need to be relative to each other.
- **eligibility**: this is what makes results trustworthy. Set `eligible_regions` to the places
  that will actually hire you, and keep `exclude_us_only` / `exclude_timezone_locked` on unless
  you can hold US-only or off-timezone roles.
- **role_exclude / false_positive_terms**: tune these after your first run, when you see which
  irrelevant titles slip through.

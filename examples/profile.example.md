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
  # Every rule below is data you supply. An empty rule is simply off: the engine never
  # invents a constraint you did not state, and empty never means "match everything".

  # Region names (lower-cased) that count as "I can legally work here". A posting restricted to a
  # country inside a region you list counts as eligible: list "latam" and a Brazil-only job
  # matches. So a region is an authorization claim, not just geography. Only list a region you can
  # work throughout: "americas" and "north america" include the United States and Canada, and
  # "emea" includes the Middle East and Africa, so listing them surfaces jobs there. If you cannot
  # work in the US, list "latam" or specific countries, not "americas".
  # Do NOT put "remote" here: it is a work mode, not a region, and would pass everything.
  eligible_regions:
    - portugal
    - europe
  # Phrases that mean a posting wants a work authorization you do not hold. Matched against
  # the posting text. This replaces the old US-only switch: put your own country's terms here.
  disqualifying_authorization_terms:
    - us citizen
    - green card
    - security clearance
    - must be based in the us
  # Phrases that signal a location lock you cannot meet.
  location_lock_terms:
    - us only
    - us-based only
    - must reside in
  # How many hours a demanded timezone may sit from your timezone_utc_offset above before it
  # rules you out. Omit (or null) to let timezone never exclude you.
  max_timezone_distance_hours: 3
  # Whether postings whose eligibility cannot be determined still show up. true favours recall
  # (see them, decide yourself); set false if you only want confirmed-eligible roles.
  include_unverified: true

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
  that will actually hire you, and fill `disqualifying_authorization_terms` and
  `location_lock_terms` with the phrases that rule you out. Leave any of them empty to turn that
  rule off. Set `max_timezone_distance_hours` if you can only overlap so many hours from your own.
- **role_exclude / false_positive_terms**: tune these after your first run, when you see which
  irrelevant titles slip through.

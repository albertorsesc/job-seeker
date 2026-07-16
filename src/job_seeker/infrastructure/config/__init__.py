"""Driven adapters: read settings from the environment and the profile from disk.

The profile is configuration, and this is the only place that knows it lives in a Markdown
file with YAML front matter. The domain receives a validated `Profile` object and never
learns where it came from, so swapping the source (a different path, a different format)
touches nothing above this package.

A malformed profile fails here, loudly, naming the file and the offending field. It must never
load as a half-populated object: a profile that is silently empty produces a run that looks
successful and is meaningless.
"""

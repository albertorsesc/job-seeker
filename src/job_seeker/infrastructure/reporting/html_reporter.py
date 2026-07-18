"""Render a run as a self-contained HTML page.

One file, no external assets: inline CSS, no scripts, no remote fonts or images, so the report
opens anywhere and leaks nothing. It is theme-aware via prefers-color-scheme.

Every piece of posting data is HTML-escaped. Titles, companies, and descriptions are untrusted
text from job boards, and a title containing "<script>" must render as characters, never as live
markup. This is presentation only: the jobs and their order are exactly what the domain ranked.
"""

from __future__ import annotations

from html import escape

from job_seeker.domain.models import ScoredJob, SearchResult, SourceCoverage

_STYLE = """
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; padding: 0 1rem;
       line-height: 1.5; }
h1 { margin-bottom: 0.25rem; }
.coverage { color: GrayText; font-size: 0.9rem; margin-bottom: 1.5rem; }
.job { border-top: 1px solid color-mix(in srgb, GrayText 30%, transparent); padding: 1rem 0; }
.job h2 { font-size: 1.1rem; margin: 0 0 0.25rem; }
.job a { text-decoration: none; }
.meta { color: GrayText; font-size: 0.9rem; }
.badge { display: inline-block; padding: 0.1rem 0.5rem; border-radius: 0.5rem; font-size: 0.8rem;
         border: 1px solid color-mix(in srgb, currentColor 40%, transparent); }
.fit { font-variant-numeric: tabular-nums; font-weight: 600; }
.matched { color: GrayText; font-size: 0.85rem; margin: 0.15rem 0 0; }
.reason { color: GrayText; font-size: 0.9rem; margin-top: 0.25rem; }
""".strip()


class HtmlReporter:
    """Serializes a SearchResult to a standalone HTML document."""

    def render(self, result: SearchResult, /) -> str:
        rows = "\n".join(_job_html(rank, scored) for rank, scored in enumerate(result.jobs, 1))
        body = (
            rows or "<p>No jobs matched. Try broadening your search or your eligibility rules.</p>"
        )
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            "<title>job-seeker report</title>\n"
            f"<style>{_STYLE}</style>\n"
            "</head><body>\n"
            "<h1>job-seeker</h1>\n"
            f'<p class="coverage">{_coverage_html(result)}</p>\n'
            f"{body}\n"
            "</body></html>\n"
        )


def _job_html(rank: int, scored: ScoredJob) -> str:
    job = scored.job
    salary = f" &middot; {escape(job.salary)}" if job.salary else ""
    return (
        '<article class="job">\n'
        f"  <h2>{rank}. {_title_html(job.title, job.url)}</h2>\n"
        f'  <p class="meta">{escape(job.company)} &middot; {escape(job.source)}{salary}</p>\n'
        f'  <p><span class="fit">fit {scored.fit.value:.0%}</span> &middot; '
        f'<span class="badge">{escape(scored.eligibility.status.value)}</span></p>\n'
        f"{_matched_html(scored.fit.matched)}"
        f'  <p class="reason">{escape(scored.eligibility.reason)}</p>\n'
        "</article>"
    )


def _matched_html(matched: dict[str, int]) -> str:
    """The fit breakdown ("python +3, rag +2"), so a reader sees why the score is what it is.

    Skill patterns are the seeker's own, but they are escaped anyway: a report escapes every value
    it renders, and a profile is still text a hostile skill list should never smuggle markup through.
    """
    if not matched:
        return ""
    parts = ", ".join(f"{escape(pattern)} +{weight}" for pattern, weight in matched.items())
    return f'  <p class="matched">{parts}</p>\n'


def _title_html(title: str, url: str) -> str:
    """The title, linked only when the URL is a safe web link.

    A board could serve a `javascript:` (or `data:`) apply URL; placing it in an href makes a
    click execute script. Only http(s) URLs become a live link; anything else renders as plain
    text, so a hostile scheme can never be clicked.
    """
    if url.lower().startswith(("http://", "https://")):
        return f'<a href="{escape(url, quote=True)}">{escape(title)}</a>'
    return escape(title)


def _coverage_html(result: SearchResult) -> str:
    complete = "complete" if result.is_complete else "partial"
    parts = [escape(f"{len(result.jobs)} jobs, {complete} coverage")]
    parts.extend(escape(_source_summary(cov)) for cov in result.coverage)
    return " &middot; ".join(parts)  # each part escaped above; the separator is a literal entity


def _source_summary(coverage: SourceCoverage) -> str:
    if coverage.failed:
        return f"{coverage.source}: failed ({coverage.error})"
    trunc = ", truncated" if coverage.truncated else ""
    return f"{coverage.source}: scanned {coverage.scanned}, kept {coverage.kept}{trunc}"

"""Render a run as a self-contained HTML page.

One file, no external assets: inline CSS, no scripts, no remote fonts or images, so the report
opens anywhere and leaks nothing. It is theme-aware via prefers-color-scheme.

Every piece of posting data is HTML-escaped. Titles, companies, and descriptions are untrusted
text from job boards, and a title containing "<script>" must render as characters, never as live
markup. This is presentation only: the jobs and their order are exactly what the domain ranked.
"""

from __future__ import annotations

from html import escape

from job_seeker.domain.models import (
    PostingHistory,
    SalaryPeriod,
    SalaryRange,
    ScoredJob,
    SearchResult,
    SourceCoverage,
)

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
.relevance { color: GrayText; font-size: 0.85rem; margin: 0.15rem 0 0; }
.reason { color: GrayText; font-size: 0.9rem; margin-top: 0.25rem; }
.badge.new { font-weight: 600; }
.seen { color: GrayText; font-size: 0.85rem; margin: 0.15rem 0 0; }
.mark { color: GrayText; font-size: 0.85rem; margin: 0.35rem 0 0; }
.mark code { background: color-mix(in srgb, GrayText 12%, transparent); padding: 0.1rem 0.35rem;
             border-radius: 0.25rem; user-select: all; }
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
    pay = _salary_text(job.salary)
    salary = f" &middot; {escape(pay)}" if pay else ""
    return (
        '<article class="job">\n'
        f"  <h2>{rank}. {_title_html(job.title, job.url)}</h2>\n"
        f'  <p class="meta">{escape(job.company)} &middot; {escape(job.source)}{salary}</p>\n'
        f'  <p><span class="fit">fit {scored.fit.value:.0%}</span> &middot; '
        f'<span class="badge">{escape(scored.eligibility.status.value)}</span>'
        f"{_history_badges(scored.history)}</p>\n"
        f"{_matched_html(scored.fit.matched)}"
        f'  <p class="relevance">relevant: {escape(scored.relevance.reason)}</p>\n'
        f'  <p class="reason">{escape(scored.eligibility.reason)}</p>\n'
        f"{_seen_html(scored.history)}"
        f"{_mark_html(scored.history)}"
        "</article>"
    )


def _salary_text(salary: SalaryRange | None) -> str:
    """Pay as a person reads it, or "" when the board published nothing.

    Formatting lives here rather than in the adapters. How a range reads is presentation, and two
    adapters each formatting their own was how they came to disagree about currency. Falls back to
    the board's own words when it published prose instead of figures.
    """
    if salary is None:
        return ""
    figures = _salary_figures(salary)
    if not figures:
        # A note is prose, not an amount: "Competitive, DOE", or an explanation of why figures were
        # withheld. Prefixing a currency to it produced "MXN board published an inverted range".
        # The currency qualifies a number, and there is no number here.
        return salary.note
    text = f"{salary.currency or ''} {figures}".strip()
    if salary.period is None:
        return text
    text = f"{text} per {salary.period.value}"
    # An hourly or monthly figure is not comparable to the annual ones beside it, so the
    # annualized equivalent is shown alongside rather than instead: the board's own number stays
    # the headline, and "~" plus "est." marks the derived one as an assumption, because it is one
    # (full time, 40 hours a week).
    annual = _annual_figures(salary)
    if annual and salary.period is not SalaryPeriod.YEAR:
        text = f"{text} (~{salary.currency or ''} {annual}/year est.)".replace("( ~", "(~")
    return text


def _annual_figures(salary: SalaryRange) -> str:
    """The annualized bounds, grouped, or "" when the period is unknown."""
    low, high = salary.annual_minimum, salary.annual_maximum
    if low is not None and high is not None and low != high:
        return f"{low:,.0f} - {high:,.0f}"
    value = low if low is not None else high
    return f"{value:,.0f}" if value is not None else ""


def _salary_figures(salary: SalaryRange) -> str:
    """The numbers, grouped, with no currency. Equal bounds render once: "150,000 - 150,000" is
    noise, not a range."""
    low, high = salary.minimum, salary.maximum
    if low is not None and high is not None and low != high:
        return f"{low:,.0f} - {high:,.0f}"
    value = low if low is not None else high
    return f"{value:,.0f}" if value is not None else ""


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
    parts = [escape(f"{len(result.jobs)} jobs, {_coverage_state(result)}")]
    parts.extend(escape(_source_summary(cov)) for cov in result.coverage)
    return " &middot; ".join(parts)  # each part escaped above; the separator is a literal entity


def _coverage_state(result: SearchResult) -> str:
    """How much of the search actually happened, naming the two facts separately.

    A board failing and a scan being capped are different events with different consequences, and
    one word for both was "partial" on every run.
    """
    if not result.all_sources_ran:
        failed = [c.source for c in result.coverage if c.failed] or ["no sources ran"]
        return f"INCOMPLETE, board(s) failed: {', '.join(failed)}"
    return "all boards ran" if result.fully_scanned else "all boards ran, scans capped"


def _source_summary(coverage: SourceCoverage) -> str:
    if coverage.failed:
        return f"{coverage.source}: failed ({coverage.error})"
    trunc = ", truncated" if coverage.truncated else ""
    return f"{coverage.source}: scanned {coverage.scanned}, kept {coverage.kept}{trunc}"


def _history_badges(history: PostingHistory | None) -> str:
    """NEW, and what the seeker already decided. Nothing at all when memory could not answer.

    Silence rather than a "not new" badge: the page cannot tell the reader something the run could
    not determine, and a badge that appears on every posting is one they stop seeing.
    """
    if history is None:
        return ""
    badges = ['<span class="badge new">NEW</span>'] if history.is_new else []
    if history.decision is not None:
        badges.append(f'<span class="badge">{escape(history.decision.value)}</span>')
    return "".join(f" &middot; {badge}" for badge in badges)


def _seen_html(history: PostingHistory | None) -> str:
    """How often this posting has been shown before, and since when.

    What lets a reader sanity-check a NEW badge instead of trusting it. A badge that is wrong is
    then visible rather than merely wrong.
    """
    if history is None or history.first_seen_at is None or history.times_seen < 1:
        return ""
    since = history.first_seen_at.astimezone().strftime("%d %b")
    times = "once" if history.times_seen == 1 else f"{history.times_seen} times"
    return f'  <p class="seen">shown {times} since {escape(since)}</p>\n'


def _mark_html(history: PostingHistory | None) -> str:
    """The command to copy, under the posting it acts on.

    The whole reason the report carries a handle at all. HTML is the default format, so without
    this line the marking loop is unreachable from the output a seeker actually looks at: they
    would have to re-run the search as JSON and dig the handle out by hand.
    """
    if history is None:
        return ""
    verb = "unmark" if history.decision is not None else "mark dismissed"
    return f'  <p class="mark"><code>job-seeker {verb} {escape(history.handle)}</code></p>\n'

"""Driven adapters: render a result to JSON, CSV, or a self-contained HTML page.

Presentation only. A reporter decides how a result looks, never which postings are in it or
what order they take: that ranking already happened in the domain. A reporter that filters is
a bug, because it makes the JSON and the HTML disagree about what the run found.
"""

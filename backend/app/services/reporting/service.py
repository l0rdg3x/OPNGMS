"""Render a ReportContext to a PDF via WeasyPrint, with remote resource fetching disabled."""
from __future__ import annotations

from weasyprint import HTML


def _blocked_fetcher(url: str):
    # Defense-in-depth: reports never fetch remote/local resources (CSS + SVG are inlined).
    raise ValueError(f"remote resource fetching is disabled in reports: {url!r}")


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=_blocked_fetcher).write_pdf()

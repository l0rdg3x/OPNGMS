"""Jinja2 environment (autoescape ON) and HTML rendering for reports."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from app.services.reporting.context import ReportContext

_TEMPLATES = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(default=True, default_for_string=True),
)


def _css() -> str:
    return (_TEMPLATES / "report.css").read_text(encoding="utf-8")


def render_html(ctx: ReportContext) -> str:
    template = _env.get_template("report.html.j2")
    # The CSS and our generated SVGs are trusted strings (SVG text is escaped in charts.py),
    # so they are marked safe; ALL report DATA is auto-escaped by Jinja.
    return template.render(ctx=ctx, css=Markup(_css()), Markup=Markup)

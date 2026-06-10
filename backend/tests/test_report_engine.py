from datetime import datetime, timezone

from app.services.reporting.context import ReportContext
from app.services.reporting.i18n import report_text
from app.services.reporting.service import html_to_pdf
from app.services.reporting.template import render_html


def _ctx():
    return ReportContext(
        tenant_name="Acme Corp",
        title="Security Report",
        timezone="UTC",
        owner=None,
        range_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        range_to=datetime(2026, 6, 8, tzinfo=timezone.utc),
        sections=[],
        t=report_text("en"),
    )


def test_render_html_contains_title_and_tenant():
    html = render_html(_ctx())
    assert "Security Report" in html
    assert "Acme Corp" in html
    assert "Table of contents" in html


def test_html_to_pdf_produces_valid_pdf():
    pdf = html_to_pdf(render_html(_ctx()))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000


def test_report_data_is_html_escaped():
    ctx = _ctx()
    ctx.tenant_name = "<script>alert(1)</script>"
    html = render_html(ctx)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html

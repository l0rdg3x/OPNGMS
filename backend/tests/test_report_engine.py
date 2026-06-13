from datetime import datetime, timezone

import pytest

from app.services.reporting.context import ReportContext
from app.services.reporting.i18n import report_text
from app.services.reporting.service import html_to_pdf
from app.services.reporting.template import render_html


def _ctx(locale: str = "en"):
    return ReportContext(
        tenant_name="Acme Corp",
        title="Security Report",
        timezone="UTC",
        owner=None,
        range_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        range_to=datetime(2026, 6, 8, tzinfo=timezone.utc),
        sections=[],
        t=report_text(locale),
        locale=locale,
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


def test_html_sets_lang_and_dir_for_ltr_and_rtl():
    # LTR locale: lang propagated, dir=ltr.
    html_ja = render_html(_ctx("ja"))
    assert 'lang="ja"' in html_ja
    assert 'dir="ltr"' in html_ja
    # RTL locale (Arabic): dir=rtl drives WeasyPrint's mirrored layout.
    html_ar = render_html(_ctx("ar"))
    assert 'lang="ar"' in html_ar
    assert 'dir="rtl"' in html_ar


@pytest.mark.parametrize("locale", ["ar", "ja"])
def test_render_pdf_for_new_locales_does_not_raise(locale):
    """Smoke: the full HTML→PDF pipeline runs for RTL (ar) and CJK (ja) without raising.
    Does not assert glyph coverage (depends on installed fonts), only that rendering succeeds."""
    pdf = html_to_pdf(render_html(_ctx(locale)))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 1000

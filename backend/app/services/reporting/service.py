"""Render a ReportContext to a PDF via WeasyPrint, with remote resource fetching disabled."""
from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from weasyprint import HTML
from weasyprint.urls import URLFetcher

from app.repositories.report_settings import ReportSettingsRepository
from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.template import render_html

# Bound the queried range to keep aggregation cheap and avoid abusive scans.
MAX_RANGE_DAYS = 92

# Logo validation constants
MAX_LOGO_BYTES = 512 * 1024
_MAGIC = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def validate_logo(data: bytes) -> str:
    """Return the mime for an accepted PNG/JPEG (by magic bytes + size), else raise ValueError."""
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError("logo too large (max 512 KB)")
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    raise ValueError("unsupported logo format (PNG or JPEG only)")


def logo_data_uri(data: bytes | None, mime: str | None) -> str | None:
    if not data or not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


class ReportRangeError(ValueError):
    """Raised for an invalid report date range (the API maps this to HTTP 400)."""


# SSRF guard: allow ONLY inline `data:` URIs (the embedded logo) — decoded inline, no network. Every
# other scheme (http/https/file/ftp) is refused by WeasyPrint's URLFetcher, preventing any outbound
# request from report data. (URLFetcher replaces the deprecated default_url_fetcher delegation.)
_report_url_fetcher = URLFetcher(allowed_protocols=["data"])


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=_report_url_fetcher).write_pdf()


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def _validate_range(frm: datetime, to: datetime) -> None:
    if to <= frm:
        raise ReportRangeError("`to` must be after `from`")
    if to - frm > timedelta(days=MAX_RANGE_DAYS):
        raise ReportRangeError(f"report range must not exceed {MAX_RANGE_DAYS} days")


class ReportService:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def build_html(self, *, tenant_name: str, frm: datetime, to: datetime, locale: str | None = None) -> str:
        frm, to = _ensure_utc(frm), _ensure_utc(to)
        _validate_range(frm, to)
        settings = await ReportSettingsRepository(self.session, self.tenant_id).get_or_default()
        effective = locale or settings.language or "en"
        ctx_logo = logo_data_uri(settings.logo, settings.logo_mime)
        agg = ReportAggregator(self.session, self.tenant_id)
        ctx = await build_context(
            agg,
            tenant_name=tenant_name,
            timezone_name=settings.timezone,
            owner=settings.owner or None,
            frm=frm,
            to=to,
            title=settings.title,
            logo_data_uri=ctx_logo,
            locale=effective,
        )
        return render_html(ctx)

    async def build_report(self, *, tenant_name: str, frm: datetime, to: datetime, locale: str | None = None) -> bytes:
        html = await self.build_html(tenant_name=tenant_name, frm=frm, to=to, locale=locale)
        return html_to_pdf(html)

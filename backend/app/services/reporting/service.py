"""Render a ReportContext to a PDF via WeasyPrint, with remote resource fetching disabled."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from weasyprint import HTML

from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.template import render_html

# Bound the queried range to keep aggregation cheap and avoid abusive scans.
MAX_RANGE_DAYS = 92


class ReportRangeError(ValueError):
    """Raised for an invalid report date range (the API maps this to HTTP 400)."""


def _blocked_fetcher(url: str):
    # SSRF guard: this fetcher REPLACES WeasyPrint's default, so no outbound request is ever
    # made for any url(...) in the document — it raises instead of fetching. WeasyPrint then
    # drops the unresolved resource and continues (it swallows fetch errors for images/CSS
    # regardless of the exception type, verified on 69.x). Together with Jinja2 autoescape —
    # which keeps untrusted report data out of any href/src — no report data can trigger a fetch.
    raise ValueError(f"remote resource fetching is disabled in reports: {url!r}")


def html_to_pdf(html: str) -> bytes:
    return HTML(string=html, url_fetcher=_blocked_fetcher).write_pdf()


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _validate_range(frm: datetime, to: datetime) -> None:
    if to <= frm:
        raise ReportRangeError("`to` must be after `from`")
    if to - frm > timedelta(days=MAX_RANGE_DAYS):
        raise ReportRangeError(f"report range must not exceed {MAX_RANGE_DAYS} days")


class ReportService:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def build_html(
        self, *, tenant_name: str, frm: datetime, to: datetime, timezone_name: str, owner: str | None
    ) -> str:
        frm, to = _ensure_utc(frm), _ensure_utc(to)
        _validate_range(frm, to)
        agg = ReportAggregator(self.session, self.tenant_id)
        ctx = await build_context(
            agg, tenant_name=tenant_name, timezone_name=timezone_name, owner=owner, frm=frm, to=to
        )
        return render_html(ctx)

    async def build_report(
        self, *, tenant_name: str, frm: datetime, to: datetime, timezone_name: str, owner: str | None
    ) -> bytes:
        html = await self.build_html(
            tenant_name=tenant_name, frm=frm, to=to, timezone_name=timezone_name, owner=owner
        )
        return html_to_pdf(html)

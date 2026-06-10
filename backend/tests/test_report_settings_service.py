"""Task 2 tests: repository, logo validation, data-URI, url_fetcher, and settings-driven branding."""
from __future__ import annotations

import base64
import struct
import zlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.repositories.report_settings import ReportSettingsRepository
from app.services.reporting.service import (
    MAX_LOGO_BYTES,
    _report_url_fetcher,
    html_to_pdf,
    logo_data_uri,
    validate_logo,
    ReportService,
)


# ---------------------------------------------------------------------------
# Tiny PNG factory (stdlib only — no Pillow needed)
# ---------------------------------------------------------------------------

def _make_tiny_png(width: int = 2, height: int = 2) -> bytes:
    """Return a minimal valid PNG with the correct magic bytes."""

    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    raw = b""
    for _ in range(height):
        raw += b"\x00" + b"\xff\x00\x00" * width  # filter=None, red pixels
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# Minimal valid JPEG (the smallest legal JFIF SOI+EOI that passes magic check)
_TINY_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"

TINY_PNG = _make_tiny_png()


# ---------------------------------------------------------------------------
# validate_logo
# ---------------------------------------------------------------------------

def test_validate_logo_accepts_png():
    mime = validate_logo(TINY_PNG)
    assert mime == "image/png"


def test_validate_logo_accepts_jpeg():
    mime = validate_logo(_TINY_JPEG)
    assert mime == "image/jpeg"


def test_validate_logo_rejects_svg():
    with pytest.raises(ValueError, match="unsupported logo format"):
        validate_logo(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")


def test_validate_logo_rejects_oversize():
    oversize = TINY_PNG + b"\x00" * (MAX_LOGO_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        validate_logo(oversize)


def test_validate_logo_rejects_empty():
    with pytest.raises(ValueError, match="unsupported logo format"):
        validate_logo(b"")


# ---------------------------------------------------------------------------
# logo_data_uri
# ---------------------------------------------------------------------------

def test_logo_data_uri_returns_data_uri():
    uri = logo_data_uri(TINY_PNG, "image/png")
    assert uri is not None
    assert uri.startswith("data:image/png;base64,")
    # Round-trip
    encoded = uri.split(",", 1)[1]
    assert base64.b64decode(encoded) == TINY_PNG


def test_logo_data_uri_returns_none_for_missing():
    assert logo_data_uri(None, None) is None
    assert logo_data_uri(b"", "image/png") is None
    assert logo_data_uri(TINY_PNG, None) is None


# ---------------------------------------------------------------------------
# _report_url_fetcher
# ---------------------------------------------------------------------------

def test_report_url_fetcher_allows_data_uri():
    # Use a real (tiny) base64-encoded PNG data: URI
    b64 = base64.b64encode(TINY_PNG).decode()
    data_url = f"data:image/png;base64,{b64}"
    result = _report_url_fetcher(data_url)
    # WeasyPrint 60+ returns a URLFetcherResponse object (not a plain dict)
    assert result is not None
    # Must have a content_type that reflects the data: MIME
    assert hasattr(result, "content_type") or hasattr(result, "read")


def test_report_url_fetcher_blocks_http():
    with pytest.raises(ValueError, match="remote resource fetching is disabled"):
        _report_url_fetcher("http://169.254.169.254/latest/meta-data/")


def test_report_url_fetcher_blocks_https():
    with pytest.raises(ValueError, match="remote resource fetching is disabled"):
        _report_url_fetcher("https://evil.example.com/x")


def test_report_url_fetcher_blocks_file():
    with pytest.raises(ValueError, match="remote resource fetching is disabled"):
        _report_url_fetcher("file:///etc/passwd")


# ---------------------------------------------------------------------------
# html_to_pdf with data: URI logo and blocked http
# ---------------------------------------------------------------------------

def test_html_to_pdf_with_logo_data_uri():
    """An HTML page embedding a PNG via data: URI must produce a valid PDF."""
    b64 = base64.b64encode(TINY_PNG).decode()
    html = f'<html><body><img src="data:image/png;base64,{b64}" /></body></html>'
    pdf = html_to_pdf(html)
    assert pdf[:5] == b"%PDF-"


def test_html_to_pdf_drops_http_img():
    """An HTML page with an http:// image must still produce a valid PDF (resource dropped)."""
    html = '<html><body><img src="http://169.254.169.254/x" /></body></html>'
    pdf = html_to_pdf(html)
    assert pdf[:5] == b"%PDF-"


# ---------------------------------------------------------------------------
# Repository tests (require DB)
# ---------------------------------------------------------------------------

async def test_repository_get_or_default_returns_unsaved_default(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        settings = await repo.get_or_default()
    # Not persisted, but has sensible defaults
    assert settings.title == "Security & Activity Report"
    assert settings.timezone == "UTC"
    assert settings.logo is None


async def test_repository_upsert_creates_row(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        row = await repo.upsert(title="Custom Title", owner="Acme", timezone="Europe/Rome")
        await s.commit()
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        row2 = await repo.get()
    assert row2 is not None
    assert row2.title == "Custom Title"
    assert row2.owner == "Acme"
    assert row2.timezone == "Europe/Rome"


async def test_repository_set_logo_and_clear(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO report_settings (tenant_id) VALUES (:t) ON CONFLICT DO NOTHING"),
            {"t": tenant_a},
        )
        await s.commit()
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        await repo.set_logo(TINY_PNG, "image/png")
        await s.commit()
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        row = await repo.get()
    assert row is not None
    assert row.logo == TINY_PNG
    assert row.logo_mime == "image/png"
    # Now clear
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        await repo.clear_logo()
        await s.commit()
    async with factory() as s:
        repo = ReportSettingsRepository(s, tenant_a)
        row = await repo.get()
    assert row is not None
    assert row.logo is None
    assert row.logo_mime is None


# ---------------------------------------------------------------------------
# Integration: settings-driven build_html
# ---------------------------------------------------------------------------

async def _seed_tenant_with_settings(factory, tid, *, title: str, owner: str, timezone: str,
                                      logo: bytes | None = None) -> None:
    """Insert a tenant, device, event, and optional report_settings row."""
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone_utc)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, :n, :slug, 'active')"),
            {"id": tid, "n": title[:20], "slug": str(tid)[:8]},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc,"
                " verify_tls, status, tags) VALUES (:id, :t, 'fw1', 'https://x', ''::bytea,"
                " ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip)"
                " VALUES (:t, :d, 'ids', 'k0', :tid, 'ET SCAN NMAP', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.execute(
            text(
                "INSERT INTO report_settings (tenant_id, title, owner, timezone)"
                " VALUES (:t, :title, :owner, :tz)"
            ),
            {"t": tid, "title": title, "owner": owner, "tz": timezone},
        )
        if logo is not None:
            await s.execute(
                text("UPDATE report_settings SET logo = :logo, logo_mime = 'image/png' WHERE tenant_id = :t"),
                {"logo": logo, "t": tid},
            )
        await s.commit()
    return base


timezone_utc = timezone.utc


async def test_build_html_uses_settings_title_and_owner(db_engine):
    """build_html must pick up title/owner from report_settings."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone_utc)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'BrandedCo', :slug, 'active')"),
            {"id": tid, "slug": str(tid)[:8]},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc,"
                " verify_tls, status, tags) VALUES (:id, :t, 'fw1', 'https://x', ''::bytea,"
                " ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip)"
                " VALUES (:t, :d, 'ids', 'k0', :tid, 'ET SCAN NMAP', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.execute(
            text(
                "INSERT INTO report_settings (tenant_id, title, owner, timezone)"
                " VALUES (:t, 'Monthly Ops Report', 'Ops Team', 'Europe/Rome')"
            ),
            {"t": tid},
        )
        await s.commit()

    async with factory() as s:
        html = await ReportService(s, tid).build_html(
            tenant_name="BrandedCo",
            frm=base - timedelta(hours=1),
            to=base + timedelta(hours=1),
        )

    assert "Monthly Ops Report" in html
    assert "Ops Team" in html


async def test_build_html_embeds_logo_as_data_uri(db_engine):
    """build_html must embed the logo as a data: URI in the HTML."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone_utc)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'LogoCo', :slug, 'active')"),
            {"id": tid, "slug": str(tid)[:8]},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc,"
                " verify_tls, status, tags) VALUES (:id, :t, 'fw1', 'https://x', ''::bytea,"
                " ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip)"
                " VALUES (:t, :d, 'ids', 'k0', :tid, 'ET SCAN NMAP', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.execute(
            text(
                "INSERT INTO report_settings (tenant_id, title, owner, timezone, logo, logo_mime)"
                " VALUES (:t, 'Logo Report', 'Logo Owner', 'UTC', :logo, 'image/png')"
            ),
            {"t": tid, "logo": TINY_PNG},
        )
        await s.commit()

    async with factory() as s:
        html = await ReportService(s, tid).build_html(
            tenant_name="LogoCo",
            frm=base - timedelta(hours=1),
            to=base + timedelta(hours=1),
        )

    assert 'src="data:image/png;base64,' in html
    assert "Logo Report" in html
    assert "Logo Owner" in html

"""Task 3 tests: report settings API — get/update, logo upload/delete, RBAC, CSRF, isolation."""
from __future__ import annotations

import os
import struct
import uuid
import zlib

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.main import app
from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


# ---------------------------------------------------------------------------
# Tiny PNG (stdlib only — no Pillow)
# ---------------------------------------------------------------------------

def _make_tiny_png() -> bytes:
    def chunk(ctype: bytes, data: bytes) -> bytes:
        c = ctype + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
    raw = b"\x00\xff\x00\x00" * 2 + b"\x00\xff\x00\x00" * 2
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


TINY_PNG = _make_tiny_png()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _login_superadmin(api_client, db_engine):
    """Create a tenant and a superadmin user, log in, return tenant_id."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return tid


async def _seed_members(db_engine):
    """Create a tenant, a tenant_admin, and an operator member; return (tid, admin_email, op_email)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="beta")
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        operator = await make_user(s, email="op@x.io", password="pw12345")
        await make_membership(s, user_id=operator.id, tenant_id=t.id, role="operator")
        await s.commit()
        return t.id, "admin@x.io", "op@x.io"


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


# ---------------------------------------------------------------------------
# GET /reports/settings — defaults
# ---------------------------------------------------------------------------

async def test_get_settings_returns_defaults(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/reports/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Security & Activity Report"
    assert body["timezone"] == "UTC"
    assert body["has_logo"] is False
    assert body["logo_mime"] is None


# ---------------------------------------------------------------------------
# PUT /reports/settings → GET reflects updates
# ---------------------------------------------------------------------------

async def test_put_settings_then_get_reflects_changes(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    payload = {"title": "My Custom Report", "owner": "Acme Corp", "timezone": "Europe/Rome"}
    r = await api_client.put(f"/api/tenants/{tid}/reports/settings", json=payload, headers=CSRF)
    assert r.status_code == 200
    out = r.json()
    assert out["title"] == "My Custom Report"
    assert out["owner"] == "Acme Corp"
    assert out["timezone"] == "Europe/Rome"
    assert out["has_logo"] is False

    # GET must reflect the same values
    r2 = await api_client.get(f"/api/tenants/{tid}/reports/settings")
    assert r2.status_code == 200
    body = r2.json()
    assert body["title"] == "My Custom Report"
    assert body["owner"] == "Acme Corp"
    assert body["timezone"] == "Europe/Rome"


# ---------------------------------------------------------------------------
# RBAC: operator PUT → 403 (REPORT_CONFIG is tenant_admin only)
# ---------------------------------------------------------------------------

async def test_operator_put_settings_is_forbidden(api_client, db_engine):
    tid, admin_email, op_email = await _seed_members(db_engine)
    await _login(api_client, op_email)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings",
        json={"title": "Hack", "owner": "", "timezone": "UTC"},
        headers=CSRF,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# CSRF: PUT without header → 403
# ---------------------------------------------------------------------------

async def test_put_settings_without_csrf_is_forbidden(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings",
        json={"title": "No CSRF", "owner": "", "timezone": "UTC"},
        # no CSRF header
    )
    assert r.status_code == 403


async def test_upload_logo_without_csrf_is_forbidden(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        # no CSRF header
    )
    assert r.status_code == 403


async def test_delete_logo_without_csrf_is_forbidden(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.delete(
        f"/api/tenants/{tid}/reports/settings/logo",
        # no CSRF header
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Logo upload — valid PNG
# ---------------------------------------------------------------------------

async def test_upload_valid_png_logo(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        headers=CSRF,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["has_logo"] is True
    assert body["logo_mime"] == "image/png"


async def test_get_logo_endpoint_returns_bytes(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    # Upload first
    await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        headers=CSRF,
    )
    # Then GET logo bytes
    r = await api_client.get(f"/api/tenants/{tid}/reports/settings/logo")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == TINY_PNG


async def test_get_logo_endpoint_404_when_no_logo(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/reports/settings/logo")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Logo upload — invalid (non-image) data → 400
# ---------------------------------------------------------------------------

async def test_upload_invalid_logo_svg_returns_400(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    bad_data = b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>"
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.svg", bad_data, "image/svg+xml")},
        headers=CSRF,
    )
    assert r.status_code == 400


async def test_upload_invalid_logo_plaintext_returns_400(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("file.txt", b"hello world", "text/plain")},
        headers=CSRF,
    )
    assert r.status_code == 400


async def test_upload_logo_mime_derived_from_magic_bytes_not_content_type(api_client, db_engine):
    """Even if the client lies about content_type, mime is derived from magic bytes."""
    tid = await _login_superadmin(api_client, db_engine)
    # Send TINY_PNG but claim it is image/jpeg in the multipart Content-Type
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.jpg", TINY_PNG, "image/jpeg")},  # lying about type
        headers=CSRF,
    )
    assert r.status_code == 200
    body = r.json()
    # The server must detect it as PNG via magic bytes, NOT trust the multipart claim
    assert body["logo_mime"] == "image/png"
    assert body["has_logo"] is True


# ---------------------------------------------------------------------------
# Logo delete
# ---------------------------------------------------------------------------

async def test_delete_logo_clears_it(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    # Upload first
    await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        headers=CSRF,
    )
    # Now delete
    r = await api_client.delete(f"/api/tenants/{tid}/reports/settings/logo", headers=CSRF)
    assert r.status_code == 200
    body = r.json()
    assert body["has_logo"] is False
    assert body["logo_mime"] is None

    # GET /logo now 404
    r2 = await api_client.get(f"/api/tenants/{tid}/reports/settings/logo")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# Cross-tenant isolation (under RLS via app_role_api_client)
# ---------------------------------------------------------------------------

async def test_cross_tenant_isolation(app_role_api_client, db_engine):
    """Settings written for tenant A must not be visible to tenant B."""
    # Use owner-role engine (bypasses RLS) to seed two tenants + superadmin
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await make_tenant(s, slug="isolation-a")
        tb = await make_tenant(s, slug="isolation-b")
        sa = await make_user(s, email="iso-sa@x.io", password="pw12345", is_superadmin=True)
        await s.commit()
        ta_id, tb_id = ta.id, tb.id

    # Login as superadmin via app_role client (uses opngms_app role for RLS)
    await app_role_api_client.post("/api/login", json={"email": "iso-sa@x.io", "password": "pw12345"})

    # Set custom title for tenant A
    r = await app_role_api_client.put(
        f"/api/tenants/{ta_id}/reports/settings",
        json={"title": "Tenant A Custom Title", "owner": "Owner A", "timezone": "UTC"},
        headers=CSRF,
    )
    assert r.status_code == 200

    # GET settings for tenant B must return defaults (not tenant A's title)
    r2 = await app_role_api_client.get(f"/api/tenants/{tb_id}/reports/settings")
    assert r2.status_code == 200
    body = r2.json()
    assert body["title"] != "Tenant A Custom Title"
    assert body["title"] == "Security & Activity Report"  # default


async def test_cross_tenant_logo_isolation(app_role_api_client, db_engine):
    """Logo uploaded for tenant A must not be visible to tenant B."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await make_tenant(s, slug="logo-iso-a")
        tb = await make_tenant(s, slug="logo-iso-b")
        await make_user(s, email="logo-sa@x.io", password="pw12345", is_superadmin=True)
        await s.commit()
        ta_id, tb_id = ta.id, tb.id

    await app_role_api_client.post("/api/login", json={"email": "logo-sa@x.io", "password": "pw12345"})

    # Upload logo for tenant A
    r = await app_role_api_client.put(
        f"/api/tenants/{ta_id}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        headers=CSRF,
    )
    assert r.status_code == 200
    assert r.json()["has_logo"] is True

    # Tenant B should have no logo
    r2 = await app_role_api_client.get(f"/api/tenants/{tb_id}/reports/settings")
    assert r2.status_code == 200
    assert r2.json()["has_logo"] is False

    r3 = await app_role_api_client.get(f"/api/tenants/{tb_id}/reports/settings/logo")
    assert r3.status_code == 404


# ---------------------------------------------------------------------------
# Operator logo upload → 403 (REPORT_CONFIG required)
# ---------------------------------------------------------------------------

async def test_operator_upload_logo_is_forbidden(api_client, db_engine):
    tid, admin_email, op_email = await _seed_members(db_engine)
    await _login(api_client, op_email)
    r = await api_client.put(
        f"/api/tenants/{tid}/reports/settings/logo",
        files={"file": ("logo.png", TINY_PNG, "image/png")},
        headers=CSRF,
    )
    assert r.status_code == 403


async def test_operator_delete_logo_is_forbidden(api_client, db_engine):
    tid, admin_email, op_email = await _seed_members(db_engine)
    await _login(api_client, op_email)
    r = await api_client.delete(f"/api/tenants/{tid}/reports/settings/logo", headers=CSRF)
    assert r.status_code == 403

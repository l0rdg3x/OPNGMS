"""Task 3 tests: generated reports API — store on generate, list, download, auth, cross-tenant isolation."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.main import app
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_report_api.py)
# ---------------------------------------------------------------------------

async def _login_superadmin(api_client, db_engine):
    """Create a tenant + superadmin user, log in, return tenant_id."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme-gr")
        await s.commit()
        tid = t.id
    await api_client.post("/api/setup", json={"email": "sa-gr@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa-gr@x.io", "password": "pw12345-secure"})
    return tid


async def _seed(db_engine, tid):
    """Insert one device + one IDS event for the given tenant; return the event timestamp."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc,"
                " verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                "VALUES (:t, :d, 'ids', 'k0', :tid, 'ET SCAN NMAP', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.commit()
    return base


# ---------------------------------------------------------------------------
# POST stores + GET /reports lists the history row
# ---------------------------------------------------------------------------

async def test_post_generate_stores_and_list_returns_one_row(api_client, db_engine):
    """After a successful POST /reports, GET /reports must list exactly 1 on_demand row."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    frm = (base - timedelta(hours=1)).isoformat()
    to_ = (base + timedelta(hours=1)).isoformat()

    r = await api_client.post(f"/api/tenants/{tid}/reports", json={"from": frm, "to": to_}, headers=csrf_headers(api_client))
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"

    r2 = await api_client.get(f"/api/tenants/{tid}/reports")
    assert r2.status_code == 200
    rows = r2.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "on_demand"
    assert row["size"] > 0
    # period must match what we sent (UTC-normalised ISO strings are equal in value)
    assert "pdf" not in row  # no pdf bytes in list response


async def test_list_period_matches_request(api_client, db_engine):
    """The period_from/period_to in the list must match the generate request."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    frm_dt = base - timedelta(hours=1)
    to_dt = base + timedelta(hours=1)

    await api_client.post(
        f"/api/tenants/{tid}/reports",
        json={"from": frm_dt.isoformat(), "to": to_dt.isoformat()},
        headers=csrf_headers(api_client),
    )
    r = await api_client.get(f"/api/tenants/{tid}/reports")
    row = r.json()[0]
    # period_from and period_to are returned as ISO datetimes; compare as datetimes
    assert datetime.fromisoformat(row["period_from"]).replace(tzinfo=timezone.utc) == frm_dt
    assert datetime.fromisoformat(row["period_to"]).replace(tzinfo=timezone.utc) == to_dt


# ---------------------------------------------------------------------------
# GET /reports/{id}/download
# ---------------------------------------------------------------------------

async def test_download_returns_pdf_bytes(api_client, db_engine):
    """Download of a known report id must return application/pdf starting with %PDF-."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)

    await api_client.post(
        f"/api/tenants/{tid}/reports",
        json={"from": (base - timedelta(hours=1)).isoformat(), "to": (base + timedelta(hours=1)).isoformat()},
        headers=csrf_headers(api_client),
    )
    rows = (await api_client.get(f"/api/tenants/{tid}/reports")).json()
    report_id = rows[0]["id"]

    r = await api_client.get(f"/api/tenants/{tid}/reports/{report_id}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


async def test_download_unknown_id_returns_404(api_client, db_engine):
    """A random UUID that was never stored must return 404."""
    tid = await _login_superadmin(api_client, db_engine)
    random_id = uuid.uuid4()
    r = await api_client.get(f"/api/tenants/{tid}/reports/{random_id}/download")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Auth: GET /reports requires a valid session
# ---------------------------------------------------------------------------

async def test_list_requires_auth(db_engine):
    """GET /reports without a session cookie must return 401 (or 404 for unknown tenant)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{uuid.uuid4()}/reports")
    assert r.status_code in (401, 404)


# ---------------------------------------------------------------------------
# Cross-tenant isolation under app_role_api_client (RLS)
# ---------------------------------------------------------------------------

async def _seed_tenant_with_report(db_engine, app_role_client, slug: str, sa_email: str):
    """Create a tenant + superadmin, log in (as superadmin), seed data, POST generate.
    Returns (tenant_id, report_id).  Uses the owner engine to seed the data."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug=slug)
        await make_user(s, email=sa_email, password="pw12345-secure", is_superadmin=True)
        await s.commit()
        tid = t.id

    # Seed device + event (owner engine, bypasses RLS)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc,"
                " verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                "VALUES (:t, :d, 'ids', 'k0', :tid, 'SIG', '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid},
        )
        await s.commit()

    # Login and generate (creates a row in generated_reports for this tenant)
    await app_role_client.post("/api/login", json={"email": sa_email, "password": "pw12345-secure"})
    r = await app_role_client.post(
        f"/api/tenants/{tid}/reports",
        json={
            "from": (base - timedelta(hours=1)).isoformat(),
            "to": (base + timedelta(hours=1)).isoformat(),
        },
        headers=csrf_headers(app_role_client),
    )
    assert r.status_code == 200, f"generate failed for {slug}: {r.text}"
    rows = (await app_role_client.get(f"/api/tenants/{tid}/reports")).json()
    assert len(rows) == 1
    return tid, rows[0]["id"]


async def test_cross_tenant_list_isolation(app_role_api_client, db_engine):
    """RLS: tenant B's session must see an empty list even after tenant A stored a report."""
    ta_id, _ = await _seed_tenant_with_report(
        db_engine, app_role_api_client, slug="iso-list-a", sa_email="iso-list-sa-a@x.io"
    )
    # Create tenant B (different user so we can log in as B)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tb = await make_tenant(s, slug="iso-list-b")
        await make_user(s, email="iso-list-sa-b@x.io", password="pw12345-secure", is_superadmin=True)
        await s.commit()
        tb_id = tb.id

    # Login as tenant B's superadmin
    await app_role_api_client.post("/api/login", json={"email": "iso-list-sa-b@x.io", "password": "pw12345-secure"})
    r = await app_role_api_client.get(f"/api/tenants/{tb_id}/reports")
    assert r.status_code == 200
    assert r.json() == []  # B sees no reports


async def test_cross_tenant_download_isolation(app_role_api_client, db_engine):
    """RLS: tenant B downloading tenant A's report_id must get 404 (row hidden by RLS)."""
    ta_id, report_a_id = await _seed_tenant_with_report(
        db_engine, app_role_api_client, slug="iso-dl-a", sa_email="iso-dl-sa-a@x.io"
    )
    # Create tenant B
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tb = await make_tenant(s, slug="iso-dl-b")
        await make_user(s, email="iso-dl-sa-b@x.io", password="pw12345-secure", is_superadmin=True)
        await s.commit()
        tb_id = tb.id

    # Login as B and try to download A's report via B's tenant prefix
    await app_role_api_client.post("/api/login", json={"email": "iso-dl-sa-b@x.io", "password": "pw12345-secure"})
    r = await app_role_api_client.get(f"/api/tenants/{tb_id}/reports/{report_a_id}/download")
    assert r.status_code == 404  # RLS hides A's row from B's session


# ---------------------------------------------------------------------------
# Existing settings routes are not broken
# ---------------------------------------------------------------------------

async def test_settings_route_still_accessible(api_client, db_engine):
    """GET /reports/settings must still work after adding new /reports routes."""
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/reports/settings")
    assert r.status_code == 200
    body = r.json()
    assert "title" in body


async def test_range_error_does_not_store(api_client, db_engine):
    """A request with an oversized range must 400 and must NOT store a generated_reports row."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    r = await api_client.post(
        f"/api/tenants/{tid}/reports",
        json={
            "from": (base - timedelta(days=120)).isoformat(),
            "to": base.isoformat(),
        },
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 400

    r2 = await api_client.get(f"/api/tenants/{tid}/reports")
    assert r2.status_code == 200
    assert r2.json() == []  # nothing stored on error

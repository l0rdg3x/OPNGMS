import os
import uuid
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.main import app
from app.services.reporting.service import ReportService
from tests.conftest import csrf_headers
from tests.factories import make_tenant


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid


async def _seed(db_engine, tid):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
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


async def test_generate_report_returns_pdf(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    body = {"from": (base - timedelta(hours=1)).isoformat(), "to": (base + timedelta(hours=1)).isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


async def test_generate_report_requires_csrf(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    body = {"from": "2026-06-09T11:00:00Z", "to": "2026-06-09T13:00:00Z"}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body)  # no CSRF header
    assert r.status_code == 403


async def test_generate_report_requires_auth(db_engine):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.post(
            f"/api/tenants/{uuid.uuid4()}/reports",
            json={"from": "2026-06-09T11:00:00Z", "to": "2026-06-09T13:00:00Z"},
            headers={"X-OPNGMS-CSRF": "anon"},
        )
    assert r.status_code in (401, 404)


async def test_report_data_not_remotely_fetched(api_client, db_engine):
    """A hostile URL embedded as an IDS signature must never trigger an outbound fetch:
    the report still renders (the URL is escaped text, and WeasyPrint's url_fetcher is blocked)."""
    tid = await _login_superadmin(api_client, db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                "VALUES (:t, :d, 'ids', 'k0', :tid, :name, '10.0.0.9', '8.8.4.4')"
            ),
            {"t": base, "d": did, "tid": tid,
             "name": "http://169.254.169.254/latest/meta-data/"},
        )
        await s.commit()
    body = {"from": (base - timedelta(hours=1)).isoformat(), "to": (base + timedelta(hours=1)).isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 200
    assert r.content[:5] == b"%PDF-"


async def test_generate_report_rejects_oversized_range(api_client, db_engine):
    """A range wider than MAX_RANGE_DAYS is rejected with 400 (bounds aggregation cost)."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    body = {"from": (base - timedelta(days=120)).isoformat(), "to": base.isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 400


async def test_generate_report_rejects_inverted_range(api_client, db_engine):
    """`to` must be after `from` -> 400."""
    tid = await _login_superadmin(api_client, db_engine)
    base = await _seed(db_engine, tid)
    body = {"from": (base + timedelta(hours=1)).isoformat(), "to": (base - timedelta(hours=1)).isoformat()}
    r = await api_client.post(f"/api/tenants/{tid}/reports", json=body, headers=csrf_headers(api_client))
    assert r.status_code == 400


async def test_report_is_tenant_isolated_under_rls(db_engine):
    """Under the real opngms_app role (RLS), tenant A's report HTML must not contain tenant B's IDS signature."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    da, db_ = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for tid, slug in [(ta, "a"), (tb, "b")]:
            await s.execute(text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, :slug, :slug, 'active')"),
                            {"id": tid, "slug": slug})
        for tid, did, name in [(ta, da, "A-ONLY-SIG"), (tb, db_, "B-ONLY-SIG")]:
            await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                                 "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"),
                            {"id": did, "t": tid, "n": f"fw-{name}"})
            await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                                 "VALUES (:t, :d, 'ids', 'k', :tid, :name, '10.0.0.1', '8.8.8.8')"),
                            {"t": base, "d": did, "tid": tid, "name": name})
        await s.commit()
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        f2 = async_sessionmaker(engine, expire_on_commit=False)
        async with f2() as s:
            await set_tenant_context(s, ta)
            html = await ReportService(s, ta).build_html(
                tenant_name="A", frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
            assert "A-ONLY-SIG" in html
            assert "B-ONLY-SIG" not in html      # RLS hides tenant B
    finally:
        await engine.dispose()

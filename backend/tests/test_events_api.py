import uuid
from datetime import datetime, timedelta, timezone

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from tests.factories import make_tenant, make_user


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid


async def _insert_device(db_engine, tenant_id, name="fw1", status="reachable"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, :st, '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name, "st": status},
        )
        await s.commit()
    return did


async def _seed_events(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        for i, (src, name) in enumerate([("ids", "ET SCAN"), ("ids", "ET POLICY"), ("dns", "example.com")]):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, :src, :k, :tid, :name, '10.0.0.5')"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "src": src,
                 "k": f"k{i}", "tid": tenant_id, "name": name},
            )
        await s.commit()
    return base


async def test_events_endpoint_returns_most_recent_first(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_events(db_engine, tid, did)
    r = await api_client.get(f"/api/tenants/{tid}/events")
    assert r.status_code == 200
    body = r.json()["items"]
    assert [e["name"] for e in body] == ["example.com", "ET POLICY", "ET SCAN"]
    # the out-schema exposes the full normalized record
    assert body[0]["source"] == "dns"
    assert body[0]["src_ip"] == "10.0.0.5"
    assert body[0]["attributes"] == {}


async def test_events_endpoint_filters_by_source(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_events(db_engine, tid, did)
    r = await api_client.get(f"/api/tenants/{tid}/events", params={"source": "ids"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert {e["source"] for e in items} == {"ids"}
    assert [e["name"] for e in items] == ["ET POLICY", "ET SCAN"]


async def test_events_endpoint_respects_limit(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_events(db_engine, tid, did)
    r = await api_client.get(f"/api/tenants/{tid}/events", params={"limit": 2})
    assert r.status_code == 200
    body = r.json()["items"]
    assert len(body) == 2  # the 2 most recent
    assert [e["name"] for e in body] == ["example.com", "ET POLICY"]


async def test_events_endpoint_naive_from_does_not_500(api_client, db_engine):
    """A naive `from` (without Z/offset) must not cause a 500: it is normalized to UTC."""
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_events(db_engine, tid, did)
    r = await api_client.get(
        f"/api/tenants/{tid}/events", params={"from": "2026-01-01T00:00:00"}
    )
    assert r.status_code == 200
    # naive `from` (well before the seeded events) -> all three returned
    assert len(r.json()["items"]) == 3


async def _seed_top_events(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        seed = [("1.1.1.1", "a"), ("1.1.1.1", "b"), ("2.2.2.2", "c")]
        for i, (ip, key) in enumerate(seed):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, src_ip, name) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :ip, 'sig')"
                ),
                {"t": base, "d": device_id, "k": key, "tid": tenant_id, "ip": ip},
            )
        await s.commit()


async def test_events_top_ranks_by_field(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    await _seed_top_events(db_engine, tid, did)
    r = await api_client.get(f"/api/tenants/{tid}/events/top", params={"field": "src_ip"})
    assert r.status_code == 200
    body = r.json()
    # most frequent first
    assert [(e["value"], e["count"]) for e in body] == [("1.1.1.1", 2), ("2.2.2.2", 1)]


async def test_events_top_rejects_non_whitelisted_field(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/events/top", params={"field": "bogus"})
    assert r.status_code == 400


async def test_events_top_rejects_injection_field(api_client, db_engine):
    """An injection string as `field` is rejected by the allowlist (400), before the repository."""
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(
        f"/api/tenants/{tid}/events/top",
        params={"field": "tenant_id; DROP TABLE events"},
    )
    assert r.status_code == 400


async def test_events_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    # new client without a session cookie
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{tid}/events")
    assert r.status_code == 401


async def test_events_forbidden_without_membership(api_client, db_engine):
    """A non-superadmin user without a membership on the tenant gets a 403.

    All tenant roles have DEVICE_VIEW, so the only realistic 403 is the
    absence of a membership: tenant_context fails with "Tenant access denied".
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        # first user (superadmin) created directly so /api/setup is blocked;
        # the user under test is non-superadmin and without a membership on this tenant.
        await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await make_user(s, email="other@x.io", password="pw12345-secure", is_superadmin=False)
        await s.commit()
        tid = t.id
    # new client to avoid reusing any cookies
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        login = await c.post(
            "/api/login", json={"email": "other@x.io", "password": "pw12345-secure"}
        )
        assert login.status_code == 200
        r = await c.get(f"/api/tenants/{tid}/events")
    assert r.status_code == 403

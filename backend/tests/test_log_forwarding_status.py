import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_membership, make_user


async def _seed(db_engine, *, enabled: bool):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,cert_not_after) "
            "VALUES (:d,:t,:e,'ab','cd',:na)"),
            {"d": did, "t": tid, "e": enabled, "na": datetime(2027, 1, 1, tzinfo=UTC)})
        await s.commit()
    return tid, did


async def _login(api_client, email="op@x.io"):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200


async def test_status_includes_liveness_when_enabled(api_client, db_engine, monkeypatch):
    called = {"n": 0}

    async def fake_latest(settings, *, tenant_id, device_id):
        called["n"] += 1
        return datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    monkeypatch.setattr("app.api.log_forwarding.latest_log_at", fake_latest)
    tid, did = await _seed(db_engine, enabled=True)
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["cert_not_after"].startswith("2027-01-01")
    assert body["last_log_at"].startswith("2026-06-01")
    assert called["n"] == 1


async def test_status_skips_opensearch_when_disabled(api_client, db_engine, monkeypatch):
    called = {"n": 0}

    async def fake_latest(settings, *, tenant_id, device_id):
        called["n"] += 1
        return datetime(2026, 6, 1, tzinfo=UTC)

    monkeypatch.setattr("app.api.log_forwarding.latest_log_at", fake_latest)
    tid, did = await _seed(db_engine, enabled=False)
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert r.status_code == 200, r.text
    assert r.json()["last_log_at"] is None
    assert called["n"] == 0

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_user

pytestmark = pytest.mark.asyncio


async def _seed_one_tenant(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'Acme','acme','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
        await s.commit()
    return tid


async def test_superadmin_sees_fleet(api_client, db_engine, monkeypatch):
    async def fake_stats(settings):
        return {}
    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(t["tenant_name"] == "Acme" and t["enabled"] == 1 for t in body["tenants"])
    assert body["totals"]["enabled_devices"] >= 1


async def test_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet")
    assert r.status_code == 403

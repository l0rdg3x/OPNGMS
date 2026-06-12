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
    async def fake_stats(settings, *, window_hours=24):
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
    assert body["window"] == "24h"  # default window echoed for the UI to label


async def _seed_two_tenants(db_engine):
    """Seed two tenants with different enabled counts (via the superuser engine — WITH CHECK is
    satisfied by the per-tenant context). Returns the tenant ids by name."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ids: dict[str, uuid.UUID] = {}
    async with factory() as s:
        for slug, name, enabled in [("acme", "Acme", 2), ("beta", "Beta", 1)]:
            tid = uuid.uuid4()
            ids[name] = tid
            await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                            {"i": tid, "n": name, "sl": slug})
            await set_tenant_context(s, tid)
            for _ in range(enabled):
                did = uuid.uuid4()
                await s.execute(text(
                    "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                    "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
                await s.execute(text(
                    "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
                    "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
        await s.commit()
    return ids


async def test_superadmin_fleet_rls_isolated(app_role_api_client, db_engine, monkeypatch):
    # The whole request path runs as opngms_app (RLS enforced) — this proves the per-tenant loop
    # isolates correctly in production, not just under the owner role. A bypass bug would make every
    # tenant show the global total (Acme=3, Beta=3, total=6) instead of the isolated 2/1/3.
    async def fake_stats(settings, *, window_hours=24):
        return {}
    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_two_tenants(db_engine)
    await app_role_api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await app_role_api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await app_role_api_client.get("/api/admin/log-fleet")
    assert r.status_code == 200, r.text
    rows = {t["tenant_name"]: t for t in r.json()["tenants"]}
    assert rows["Acme"]["enabled"] == 2
    assert rows["Beta"]["enabled"] == 1
    assert r.json()["totals"]["enabled_devices"] == 3


async def test_window_param_maps_through(api_client, db_engine, monkeypatch):
    seen: dict = {}

    async def fake_stats(settings, *, window_hours=24):
        seen["window_hours"] = window_hours
        return {}

    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})

    r = await api_client.get("/api/admin/log-fleet", params={"window": "7d"})
    assert r.status_code == 200, r.text
    assert r.json()["window"] == "7d"
    assert seen["window_hours"] == 168  # 7d -> 168h

    r = await api_client.get("/api/admin/log-fleet", params={"window": "30d"})
    assert r.status_code == 200, r.text
    assert r.json()["window"] == "30d"
    assert seen["window_hours"] == 720  # 30d -> 720h

    # an unknown window falls back to the 24h default
    r = await api_client.get("/api/admin/log-fleet", params={"window": "bogus"})
    assert r.status_code == 200, r.text
    assert r.json()["window"] == "24h"
    assert seen["window_hours"] == 24


async def test_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet")
    assert r.status_code == 403


async def test_export_csv(api_client, db_engine, monkeypatch):
    async def fake_stats(settings, *, window_hours=24):
        return {}
    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet/export", params={"format": "csv", "window": "7d"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "log-fleet-7d.csv" in r.headers["content-disposition"]
    lines = r.text.strip().splitlines()
    assert lines[0].startswith("tenant_name,enabled")
    assert any(line.startswith("Acme,") for line in lines[1:])


async def test_export_pdf(api_client, db_engine, monkeypatch):
    async def fake_stats(settings, *, window_hours=24):
        return {}
    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet/export", params={"format": "pdf"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:5] == b"%PDF-"


async def test_export_invalid_format_400(api_client, db_engine):
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet/export", params={"format": "xlsx"})
    assert r.status_code == 400


async def test_export_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet/export", params={"format": "csv"})
    assert r.status_code == 403


async def test_superadmin_drills_into_tenant_devices(api_client, db_engine, monkeypatch):
    async def fake_stats(settings, tenant_id, *, window_hours=24):
        return {}  # no logs -> the enabled device is silent
    monkeypatch.setattr("app.services.log_fleet.fleet_device_log_stats", fake_stats)
    tid = await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get(f"/api/admin/log-fleet/tenants/{tid}/devices", params={"window": "7d"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tenant_name"] == "Acme"
    assert body["window"] == "7d"
    assert len(body["devices"]) == 1
    dev = body["devices"][0]
    assert dev["forwarding"] == "enabled" and dev["is_silent"] is True
    assert body["totals"]["enabled_devices"] == 1 and body["totals"]["silent_devices"] == 1


async def test_tenant_devices_unknown_tenant_404(api_client, db_engine):
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get(f"/api/admin/log-fleet/tenants/{uuid.uuid4()}/devices")
    assert r.status_code == 404


async def test_tenant_devices_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    r = await api_client.get(f"/api/admin/log-fleet/tenants/{uuid.uuid4()}/devices")
    assert r.status_code == 403

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_user

pytestmark = pytest.mark.asyncio


async def _superadmin(api_client):
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})


async def test_superadmin_get_and_set_live_push(api_client, db_engine):
    await _superadmin(api_client)
    r = await api_client.get("/api/admin/live-push")
    assert r.status_code == 200 and r.json()["enabled"] is False     # env default
    r = await api_client.put("/api/admin/live-push", json={"enabled": True}, headers=csrf_headers(api_client))
    assert r.status_code == 200 and r.json()["enabled"] is True
    r = await api_client.get("/api/admin/live-push")
    assert r.json()["enabled"] is True


async def test_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345-secure", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345-secure"})
    assert (await api_client.get("/api/admin/live-push")).status_code == 403


async def test_superadmin_get_runtime_settings(api_client, db_engine):
    await _superadmin(api_client)
    r = await api_client.get("/api/admin/settings")
    assert r.status_code == 200
    settings = {s["key"]: s for s in r.json()["settings"]}
    # only the active (consumer-wired) settings are exposed; the auth-path ones are inactive for now
    assert len(settings) == 6
    assert "session_ttl_hours" not in settings and "login_max_attempts" not in settings
    # effective == default when nothing is overridden
    assert settings["silent_alert_after_hours"]["value"] == 6 == settings["silent_alert_after_hours"]["default"]
    assert settings["silent_alert_after_hours"]["kind"] == "int"
    assert settings["silent_alert_enabled"]["value"] is True
    assert settings["firmware_poll_interval_seconds"]["kind"] == "float"


async def test_superadmin_put_runtime_settings_round_trip(api_client, db_engine):
    await _superadmin(api_client)
    r = await api_client.put(
        "/api/admin/settings",
        json={"values": {"silent_alert_after_hours": 48, "silent_alert_enabled": False}},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 200
    settings = {s["key"]: s for s in r.json()["settings"]}
    assert settings["silent_alert_after_hours"]["value"] == 48
    assert settings["silent_alert_enabled"]["value"] is False
    # persisted: a fresh GET still reflects the override
    r = await api_client.get("/api/admin/settings")
    settings = {s["key"]: s for s in r.json()["settings"]}
    assert settings["silent_alert_after_hours"]["value"] == 48
    assert settings["silent_alert_after_hours"]["default"] == 6  # default is still reported


async def test_put_runtime_settings_rejects_invalid(api_client, db_engine):
    await _superadmin(api_client)
    # out of bounds
    r = await api_client.put(
        "/api/admin/settings", json={"values": {"silent_alert_after_hours": 0}}, headers=csrf_headers(api_client)
    )
    assert r.status_code == 422
    # unknown key
    r = await api_client.put(
        "/api/admin/settings", json={"values": {"nope": 1}}, headers=csrf_headers(api_client)
    )
    assert r.status_code == 422


async def test_put_runtime_settings_rejects_inactive_key(api_client, db_engine):
    await _superadmin(api_client)
    # session_ttl_hours is a real registry key but inactive (consumer not wired) -> treated as unknown
    r = await api_client.put(
        "/api/admin/settings", json={"values": {"session_ttl_hours": 24}}, headers=csrf_headers(api_client)
    )
    assert r.status_code == 422
    assert "session_ttl_hours" in r.json()["detail"]


async def test_put_runtime_settings_requires_csrf(api_client, db_engine):
    await _superadmin(api_client)
    r = await api_client.put("/api/admin/settings", json={"values": {"silent_alert_after_hours": 24}})
    assert r.status_code == 403


async def test_non_superadmin_denied_runtime_settings(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op2@x.io", password="pw12345-secure", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op2@x.io", "password": "pw12345-secure"})
    assert (await api_client.get("/api/admin/settings")).status_code == 403
    r = await api_client.put(
        "/api/admin/settings", json={"values": {"silent_alert_after_hours": 24}}, headers=csrf_headers(api_client)
    )
    assert r.status_code == 403

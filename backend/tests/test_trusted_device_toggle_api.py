from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.app_settings import get_trusted_device_enabled
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _superadmin(api_client, db_engine, email="adm@x.io"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email=email, password="pw12345-secure", is_superadmin=True)
        await s.commit()
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def test_get_and_set_toggle(api_client, db_engine):
    await _superadmin(api_client, db_engine)
    r = await api_client.get("/api/admin/trusted-device-enabled")
    assert r.status_code == 200 and r.json()["enabled"] is True  # default on
    h = csrf_headers(api_client)
    r = await api_client.put("/api/admin/trusted-device-enabled", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_trusted_device_enabled(s, env_default=True) is False


async def test_toggle_requires_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="plain@x.io", password="pw12345-secure")
        await s.commit()
    await api_client.post("/api/login", json={"email": "plain@x.io", "password": "pw12345-secure"})
    assert (await api_client.get("/api/admin/trusted-device-enabled")).status_code == 403


async def test_status_includes_trusted_device_feature(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="u@x.io", password="pw12345-secure")
        await s.commit()
    await api_client.post("/api/login", json={"email": "u@x.io", "password": "pw12345-secure"})
    r = await api_client.get("/api/me/mfa")
    assert r.status_code == 200
    assert r.json()["trusted_devices"]["enabled"] is True

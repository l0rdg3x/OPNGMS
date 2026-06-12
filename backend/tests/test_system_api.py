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

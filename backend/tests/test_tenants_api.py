import pytest

from tests.conftest import csrf_headers

pytestmark = pytest.mark.asyncio


async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})


async def test_superadmin_can_create_and_list_tenants(api_client):
    await _login_superadmin(api_client)
    created = await api_client.post(
        "/api/tenants", json={"name": "Customer A", "slug": "cliente-a"}, headers=csrf_headers(api_client)
    )
    assert created.status_code == 201
    assert created.json()["slug"] == "cliente-a"
    listed = await api_client.get("/api/tenants")
    assert listed.status_code == 200
    assert any(t["slug"] == "cliente-a" for t in listed.json())


async def test_non_superadmin_cannot_create_tenant(api_client, db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.factories import make_user

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    resp = await api_client.post(
        "/api/tenants", json={"name": "X", "slug": "x"}, headers=csrf_headers(api_client)
    )
    assert resp.status_code == 403


async def test_unauthenticated_cannot_list_tenants(api_client):
    resp = await api_client.get("/api/tenants")
    assert resp.status_code == 401

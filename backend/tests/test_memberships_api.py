from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_tenant, make_user


async def _seed_superadmin_and_tenant(api_client, db_engine):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="member@x.io", password="pw12345-secure")
        await s.commit()
        return t.id, u.id


async def test_superadmin_assigns_membership(api_client, db_engine):
    tenant_id, user_id = await _seed_superadmin_and_tenant(api_client, db_engine)
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": str(user_id), "role": "operator"},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 201
    assert resp.json()["role"] == "operator"
    listed = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert any(m["user_id"] == str(user_id) for m in listed.json())


async def test_invalid_role_rejected(api_client, db_engine):
    tenant_id, user_id = await _seed_superadmin_and_tenant(api_client, db_engine)
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": str(user_id), "role": "wizard"},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 422

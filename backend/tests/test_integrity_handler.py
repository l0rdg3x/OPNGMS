from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})


async def test_duplicate_tenant_slug_returns_409(api_client):
    await _login_superadmin(api_client)
    body = {"name": "Acme", "slug": "acme"}
    assert (await api_client.post("/api/tenants", json=body, headers=CSRF)).status_code == 201
    dup = await api_client.post("/api/tenants", json=body, headers=CSRF)
    assert dup.status_code == 409


async def test_duplicate_membership_returns_409(api_client, db_engine):
    await _login_superadmin(api_client)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="m@x.io", password="pw12345")
        await s.commit()
        tenant_id, user_id = t.id, u.id
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    body = {"user_id": str(user_id), "role": "operator"}
    first = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships", json=body, headers=CSRF
    )
    assert first.status_code == 201
    dup = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships", json=body, headers=CSRF
    )
    assert dup.status_code == 409


async def test_membership_nonexistent_user_returns_409(api_client, db_engine):
    import uuid

    await _login_superadmin(api_client)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tenant_id = t.id
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": str(uuid.uuid4()), "role": "operator"},
        headers=CSRF,
    )
    assert resp.status_code == 409

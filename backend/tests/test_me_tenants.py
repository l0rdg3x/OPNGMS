from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user


async def test_member_sees_only_their_tenants(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a", name="Alpha")
        await make_tenant(s, slug="b", name="Beta")  # not a member
        u = await make_user(s, email="u@x.io", password="pw12345-secure")
        await make_membership(s, user_id=u.id, tenant_id=a.id, role="operator")
        await s.commit()
    await api_client.post("/api/login", json={"email": "u@x.io", "password": "pw12345-secure"})
    resp = await api_client.get("/api/me/tenants")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["slug"] for t in body] == ["a"]
    assert body[0]["role"] == "operator"


async def test_superadmin_sees_all_tenants(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_tenant(s, slug="a")
        await make_tenant(s, slug="b")
        await s.commit()
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    resp = await api_client.get("/api/me/tenants")
    assert resp.status_code == 200
    assert {t["slug"] for t in resp.json()} == {"a", "b"}


async def test_me_tenants_requires_auth(api_client):
    assert (await api_client.get("/api/me/tenants")).status_code == 401

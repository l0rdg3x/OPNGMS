import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="t1")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await make_user(s, email="out@x.io", password="pw12345")
        await s.commit()
        return t.id


async def test_member_can_access_tenant_scope(api_client, db_engine):
    tenant_id = await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert resp.status_code == 200


async def test_non_member_denied_tenant_scope(api_client, db_engine):
    tenant_id = await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "out@x.io", "password": "pw12345"})
    resp = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert resp.status_code == 403


async def test_unknown_tenant_404(api_client, db_engine):
    await _seed(db_engine)
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.get(f"/api/tenants/{uuid.uuid4()}/memberships")
    assert resp.status_code == 404

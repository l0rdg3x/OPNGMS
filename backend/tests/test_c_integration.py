import pytest

from app.services.onboarding import ProbeResult, get_prober
from tests.conftest import csrf_headers

pytestmark = pytest.mark.asyncio


async def test_device_lifecycle(api_client, db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.main import app
    from tests.factories import make_membership, make_tenant, make_user

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        op = await make_user(s, email="op@x.io", password="pw12345-secure")
        await make_membership(s, user_id=op.id, tenant_id=t.id, role="operator")
        await s.commit()
        tenant_id = t.id

    async def _fake(*a, **k):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345-secure"})

    # create -> reachable
    c = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw", "base_url": "https://fw", "api_key": "k", "api_secret": "s"},
        headers=csrf_headers(api_client),
    )
    assert c.status_code == 201
    did = c.json()["id"]
    # get
    assert (await api_client.get(f"/api/tenants/{tenant_id}/devices/{did}")).status_code == 200
    # update
    u = await api_client.patch(
        f"/api/tenants/{tenant_id}/devices/{did}", json={"site": "HQ"}, headers=csrf_headers(api_client)
    )
    assert u.json()["site"] == "HQ"
    # rotate
    assert (await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{did}/rotate-secret",
        json={"api_key": "k2", "api_secret": "s2"}, headers=csrf_headers(api_client),
    )).status_code == 200
    # test-connection
    assert (await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{did}/test-connection", headers=csrf_headers(api_client)
    )).json()["status"] == "reachable"
    # delete
    assert (await api_client.delete(
        f"/api/tenants/{tenant_id}/devices/{did}", headers=csrf_headers(api_client)
    )).status_code == 204

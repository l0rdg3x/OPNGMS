from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user


async def _seed_and_login(api_client, db_engine):
    from app.main import app
    from app.services.onboarding import ProbeResult, get_prober

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        tenant_id = t.id

    async def _fake(*a, **k):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    return tenant_id


async def _create(api_client, tenant_id, name="fw1"):
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": name, "base_url": "https://fw1", "api_key": "k", "api_secret": "s"},
        headers=csrf_headers(api_client),
    )
    return r.json()["id"]


async def test_update_device_fields(api_client, db_engine):
    tenant_id = await _seed_and_login(api_client, db_engine)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.patch(
        f"/api/tenants/{tenant_id}/devices/{device_id}",
        json={"name": "fw1-renamed", "tags": ["edge"]},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "fw1-renamed"
    assert resp.json()["tags"] == ["edge"]


async def test_update_nonexistent_404(api_client, db_engine):
    import uuid

    tenant_id = await _seed_and_login(api_client, db_engine)
    resp = await api_client.patch(
        f"/api/tenants/{tenant_id}/devices/{uuid.uuid4()}",
        json={"name": "x"},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 404


async def test_delete_device(api_client, db_engine):
    tenant_id = await _seed_and_login(api_client, db_engine)
    device_id = await _create(api_client, tenant_id)
    d = await api_client.delete(f"/api/tenants/{tenant_id}/devices/{device_id}", headers=csrf_headers(api_client))
    assert d.status_code == 204
    g = await api_client.get(f"/api/tenants/{tenant_id}/devices/{device_id}")
    assert g.status_code == 404

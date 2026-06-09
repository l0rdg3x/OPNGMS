from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_login(api_client, db_engine, reachable=True):
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
        return ProbeResult(
            reachable=reachable,
            firmware_version="24.7" if reachable else None,
            error=None if reachable else "AuthError: x",
        )

    app.dependency_overrides[get_prober] = lambda: _fake
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    return tenant_id


async def _create(api_client, tenant_id):
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw1", "base_url": "https://fw1", "api_key": "k0", "api_secret": "s0"},
        headers=CSRF,
    )
    return r.json()["id"]


async def test_test_connection_endpoint(api_client, db_engine):
    tenant_id = await _seed_login(api_client, db_engine, reachable=True)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/test-connection", headers=CSRF
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "reachable"
    assert resp.json()["firmware_version"] == "24.7"


async def test_rotate_secret_changes_ciphertext(api_client, db_engine):
    from app.core import crypto

    tenant_id = await _seed_login(api_client, db_engine, reachable=True)
    device_id = await _create(api_client, tenant_id)
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/rotate-secret",
        json={"api_key": "k1", "api_secret": "s1"},
        headers=CSRF,
    )
    assert resp.status_code == 200
    assert "api_key" not in resp.json() and "api_secret" not in resp.json()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(select(Device).where(Device.id == device_id))).scalar_one()
        assert crypto.decrypt(row.api_key_enc) == "k1"
        assert crypto.decrypt(row.api_secret_enc) == "s1"

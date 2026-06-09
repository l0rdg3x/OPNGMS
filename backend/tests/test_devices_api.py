from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from tests.factories import make_membership, make_tenant, make_user

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _seed_admin_member(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


def _override_prober(reachable=True, version="24.7", error=None):
    from app.main import app
    from app.services.onboarding import ProbeResult, get_prober

    async def _fake(*args, **kwargs):
        return ProbeResult(reachable=reachable, firmware_version=version, error=error)

    app.dependency_overrides[get_prober] = lambda: _fake


async def test_create_device_reachable_and_secrets_hidden(api_client, db_engine):
    tenant_id = await _seed_admin_member(db_engine)
    _override_prober(reachable=True, version="24.7")
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw1", "base_url": "https://fw1", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "reachable"
    assert body["firmware_version"] == "24.7"
    assert "api_key" not in body and "api_secret" not in body
    assert "api_key_enc" not in body and "api_secret_enc" not in body


async def test_create_device_unverified_when_probe_fails(api_client, db_engine):
    tenant_id = await _seed_admin_member(db_engine)
    _override_prober(reachable=False, version=None, error="AuthError: x")
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw2", "base_url": "https://fw2", "api_key": "k", "api_secret": "bad"},
        headers=CSRF,
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "unverified"


async def test_secrets_encrypted_at_rest(api_client, db_engine):
    from app.core import crypto

    tenant_id = await _seed_admin_member(db_engine)
    _override_prober()
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "fw3", "base_url": "https://fw3", "api_key": "the-key", "api_secret": "the-secret"},
        headers=CSRF,
    )
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (await s.execute(select(Device).where(Device.name == "fw3"))).scalar_one()
        assert bytes(row.api_secret_enc) != b"the-secret"  # cifrato
        assert crypto.decrypt(row.api_secret_enc) == "the-secret"  # decifrabile


async def test_read_only_can_list_but_not_create(api_client, db_engine):
    tenant_id = await _seed_admin_member(db_engine)
    _override_prober()
    await api_client.post("/api/login", json={"email": "ro@x.io", "password": "pw12345"})
    listed = await api_client.get(f"/api/tenants/{tenant_id}/devices")
    assert listed.status_code == 200
    denied = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "x", "base_url": "https://x", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert denied.status_code == 403


async def test_create_device_rejects_non_https_base_url(api_client, db_engine):
    tenant_id = await _seed_admin_member(db_engine)
    _override_prober()
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345"})
    resp = await api_client.post(
        f"/api/tenants/{tenant_id}/devices",
        json={"name": "x", "base_url": "http://127.0.0.1", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert resp.status_code == 422  # rifiutato dal validatore di schema (non-https)

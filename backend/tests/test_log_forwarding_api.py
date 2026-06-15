import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.queue import get_enqueuer
from app.main import app
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user, seed_syslog_ca


class FakeClient:
    async def import_ca(self, pem, *, descr): return "ca"
    async def import_cert(self, c, k, *, descr): return "cert"
    async def add_syslog_destination(self, *, hostname, port, certificate_uuid, description="x"): return "dest"
    async def delete_syslog_destination(self, u): return {}
    async def delete_cert(self, u): return {}


async def _seed(db_engine):
    await seed_syslog_ca(db_engine)  # provisioning needs the CA pre-created owner-side
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345-secure")
        ro = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"}); assert r.status_code == 200


async def test_enable_then_status(api_client, db_engine, monkeypatch):
    import app.api.log_forwarding as mod
    monkeypatch.setattr(mod, "_client", lambda device: FakeClient())
    tid, did = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/enable", headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and r.json()["cert_serial"]
    g = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert g.json()["enabled"] is True


async def test_read_only_denied(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/enable", headers=csrf_headers(api_client))
    assert r.status_code == 403


def _override_enqueuer(monkeypatch):
    """Install a recording enqueuer override (wins over the conftest no-op set in api_client).

    Uses monkeypatch.setitem so the override is restored after the test, not left dangling."""
    calls: list = []

    async def _fake_enqueue(name, *args, defer_until=None):
        calls.append((name, args, defer_until))

    monkeypatch.setitem(app.dependency_overrides, get_enqueuer, lambda: _fake_enqueue)
    return calls


async def test_revoke_enqueues_crl_refresh(api_client, db_engine, monkeypatch):
    import app.api.log_forwarding as mod
    monkeypatch.setattr(mod, "_client", lambda device: FakeClient())
    tid, did = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    # Enable first so the device is forwarding (revoke requires an active row).
    e = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/enable",
                              headers=csrf_headers(api_client))
    assert e.status_code == 200, e.text
    calls = _override_enqueuer(monkeypatch)
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/revoke",
                              json={"reason": "stolen key"}, headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    assert calls == [("refresh_syslog_crl_job", (), None)]


async def test_revoke_not_forwarding_does_not_enqueue(api_client, db_engine, monkeypatch):
    import app.api.log_forwarding as mod
    monkeypatch.setattr(mod, "_client", lambda device: FakeClient())
    tid, did = await _seed(db_engine)  # device never enabled -> revoke 409s
    await _login(api_client, "admin@x.io")
    calls = _override_enqueuer(monkeypatch)
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/revoke",
                              json={"reason": "x"}, headers=csrf_headers(api_client))
    assert r.status_code == 409
    assert calls == []

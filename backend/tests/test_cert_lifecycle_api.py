import types
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


def _row(did, **kw):
    base = {"device_id": did, "enabled": True, "cert_serial": "newserial", "cert_fingerprint": "fp",
            "provisioned_at": None, "cert_not_after": None, "revoked_at": None}
    base.update(kw)
    return types.SimpleNamespace(**base)


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200


async def test_rotate_operator_ok(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, receiver_host, receiver_port):
        return _row(did, cert_serial="rotated")
    monkeypatch.setattr("app.api.log_forwarding._client", lambda device: object())
    monkeypatch.setattr("app.api.log_forwarding.rotate_device_cert", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate",
                              headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    assert r.json()["cert_serial"] == "rotated"


async def test_rotate_read_only_denied(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate")
    assert r.status_code == 403


async def test_rotate_409_when_not_forwarding(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, receiver_host, receiver_port):
        raise ValueError("device is not currently forwarding")
    monkeypatch.setattr("app.api.log_forwarding._client", lambda device: object())
    monkeypatch.setattr("app.api.log_forwarding.rotate_device_cert", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/rotate",
                              headers=csrf_headers(api_client))
    assert r.status_code == 409


async def test_revoke_operator_ok(api_client, db_engine, monkeypatch):
    tid, did = await _seed(db_engine)

    async def fake(session, *, tenant_id, device_id, client, reason):
        assert reason == "key leak"
        return _row(did, enabled=False, revoked_at=datetime(2026, 6, 1, tzinfo=UTC))
    monkeypatch.setattr("app.api.log_forwarding._client", lambda device: object())
    monkeypatch.setattr("app.api.log_forwarding.revoke_device", fake)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/revoke",
                              json={"reason": "key leak"}, headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["revoked_at"].startswith("2026-06-01")

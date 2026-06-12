import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


async def _seed(db_engine, *, status="applied", kind="alias", operation="add"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        c = ConfigChange(tenant_id=tid, device_id=did, created_by=admin.id, kind=kind,
                         operation=operation, target="A", payload={"name": "A", "type": "host"},
                         baseline_hash="", status=status)
        s.add(c)
        await s.commit()
        return tid, did, c.id


async def _login(api_client, email="admin@x.io"):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_revert_creates_and_schedules_inverse(api_client, db_engine):
    from app.core.queue import get_enqueuer
    from app.main import app

    calls = []
    async def fake_enqueue(name, *a, **k): calls.append((name, a, k))
    app.dependency_overrides[get_enqueuer] = lambda: fake_enqueue
    try:
        tid, did, cid = await _seed(db_engine)
        await _login(api_client)
        r = await api_client.post(
            f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/revert",
            headers=csrf_headers(api_client), json={})
        assert r.status_code in (200, 201), r.text
        body = r.json()
        assert body["operation"] == "delete"
        assert calls and calls[0][0] == "apply_config_change"
    finally:
        app.dependency_overrides.pop(get_enqueuer, None)


async def test_revert_rejects_non_invertible_kind(api_client, db_engine):
    tid, did, cid = await _seed(db_engine, kind="opnsense_setting")
    await _login(api_client)
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/revert",
        headers=csrf_headers(api_client), json={})
    assert r.status_code == 409


async def test_list_exposes_reverts_and_revertible(api_client, db_engine):
    tid, did, cid = await _seed(db_engine)
    await _login(api_client)
    g = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/changes")
    assert g.status_code == 200
    row = next(r for r in g.json() if r["id"] == str(cid))
    assert row["revertible"] is True
    assert row["reverts_change_id"] is None

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
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
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_tenant_admin_upserts_and_lists(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    p = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
        "recipients": ["A@x.io", "a@x.io"],
    })
    assert p.status_code == 200, p.text
    assert p.json()["recipients"] == ["a@x.io"]
    assert p.json()["next_run_at"] is not None
    g = await api_client.get(f"/api/tenants/{tid}/report-schedules")
    assert len(g.json()) == 1


async def test_weekly_requires_weekday(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": None, "hour": 4,
        "recipients": ["a@x.io"],
    })
    assert r.status_code == 400


async def test_device_must_belong_to_tenant(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": str(uuid.uuid4()), "enabled": True, "frequency": "monthly", "weekday": None,
        "hour": 4, "recipients": ["a@x.io"],
    })
    assert r.status_code == 404


async def test_read_only_denied(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
        "recipients": ["a@x.io"],
    })
    assert r.status_code == 403


async def test_send_now_enqueues(api_client, db_engine):
    from app.core.queue import get_enqueuer
    from app.main import app

    calls = []

    async def fake_enqueue(name, *args, **kwargs):
        calls.append((name, args, kwargs))

    app.dependency_overrides[get_enqueuer] = lambda: fake_enqueue
    try:
        tid, _ = await _seed(db_engine)
        await _login(api_client, "admin@x.io")
        p = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
            "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
            "recipients": ["a@x.io"]})
        sid = p.json()["id"]
        r = await api_client.post(f"/api/tenants/{tid}/report-schedules/{sid}/send-now",
                                  headers=csrf_headers(api_client))
        assert r.status_code == 202, r.text
        assert calls and calls[0][0] == "deliver_scheduled_report"
        assert calls[0][1][0] == sid and calls[0][1][1] is True
    finally:
        app.dependency_overrides.pop(get_enqueuer, None)

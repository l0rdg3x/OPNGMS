import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import get_enqueuer
from app.main import app
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user


async def _seed_members(db_engine):
    """Create a tenant with a tenant_admin and a read_only member."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        viewer = await make_user(s, email="ro@x.io", password="pw12345")
        await make_membership(s, user_id=viewer.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


async def _insert_device(db_engine, tenant_id, name="fw1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name},
        )
        await s.commit()
    return did


def _override_enqueuer():
    calls: list = []

    async def _fake_enqueue(name, *args, defer_until=None):
        calls.append((name, args, defer_until))

    app.dependency_overrides[get_enqueuer] = lambda: _fake_enqueue
    return calls


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def test_create_action_enqueues(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/firmware/action",
        json={"kind": "firmware_update"},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["kind"] == "firmware_update" and body["status"] == "scheduled"
    assert len(calls) == 1
    name, args, defer_until = calls[0]
    assert name == "run_firmware_action" and args == (body["id"],) and defer_until is None


async def test_create_action_rejects_bad_kind(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/firmware/action",
        json={"kind": "reboot_now"},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 422


async def test_plugin_action_requires_target(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/firmware/action",
        json={"kind": "plugin_install", "target": ""},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 400


async def test_read_only_forbidden_to_create_action(api_client, db_engine):
    """read_only has DEVICE_VIEW but not CONFIG_PUSH -> firmware action must be 403."""
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    _override_enqueuer()
    await _login(api_client, "ro@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/firmware/action",
        json={"kind": "firmware_update"}, headers=csrf_headers(api_client))
    assert r.status_code == 403

import uuid

from httpx import ASGITransport, AsyncClient
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
    """Override get_enqueuer with a fake recorder so no real Redis is touched."""
    calls: list = []

    async def _fake_enqueue(name, *args, defer_until=None):
        calls.append((name, args, defer_until))

    app.dependency_overrides[get_enqueuer] = lambda: _fake_enqueue
    return calls


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def _create_change(api_client, tid, did, payload=None):
    body = payload or {
        "kind": "alias",
        "operation": "set",
        "target": "myalias",
        "payload": {"name": "myalias", "content": ["1.2.3.4"]},
    }
    return await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes", json=body, headers=csrf_headers(api_client)
    )


async def test_create_change_returns_201_and_hides_internals(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    r = await _create_change(api_client, tid, did)
    assert r.status_code == 201
    body = r.json()
    assert body["device_id"] == str(did)
    assert body["kind"] == "alias"
    assert body["operation"] == "set"
    assert body["target"] == "myalias"
    assert body["status"] == "draft"
    # SECURITY: internal fields must NEVER be exposed on the out-schema.
    assert "payload" not in body
    assert "result" not in body
    assert "baseline_hash" not in body
    # the secret-bearing content value must not leak through the JSON either
    assert "1.2.3.4" not in r.text


async def test_list_changes_hides_internals(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    await _create_change(api_client, tid, did)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/config/changes")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    row = body[0]
    assert "payload" not in row
    assert "result" not in row
    assert "baseline_hash" not in row
    assert "1.2.3.4" not in r.text


async def test_preview_is_secret_safe_summary(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/preview"
    )
    assert r.status_code == 200
    p = r.json()
    assert p["operation"] == "set"
    assert p["kind"] == "alias"
    assert p["target"] == "myalias"
    assert p["new"] == {"name": "myalias", "content": ["1.2.3.4"]}


async def test_schedule_immediate_enqueues_without_defer(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/schedule",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "scheduled"
    assert r.json()["scheduled_at"] is None
    # the injected enqueuer recorded an immediate job (no defer_until)
    assert len(calls) == 1
    name, args, defer_until = calls[0]
    assert name == "apply_config_change"
    assert args == (cid,)
    assert defer_until is None


async def test_schedule_deferred_enqueues_with_defer(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    when = "2099-01-01T12:00:00+00:00"
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/schedule",
        json={"scheduled_at": when},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "scheduled"
    assert body["scheduled_at"] is not None
    # the injected enqueuer recorded a deferred job
    assert len(calls) == 1
    name, args, defer_until = calls[0]
    assert name == "apply_config_change"
    assert args == (cid,)
    assert defer_until is not None


async def test_cancel_sets_cancelled(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/cancel", headers=csrf_headers(api_client)
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


async def test_schedule_conflict_on_non_draft(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    _override_enqueuer()
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    # cancel first -> status 'cancelled'
    await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/cancel", headers=csrf_headers(api_client)
    )
    # now scheduling must 409
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/schedule",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 409


async def test_cancel_conflict_on_cancelled(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/cancel", headers=csrf_headers(api_client)
    )
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/cancel", headers=csrf_headers(api_client)
    )
    assert r.status_code == 409


async def test_read_only_forbidden_to_schedule(api_client, db_engine):
    """read_only has DEVICE_VIEW but NOT CONFIG_PUSH -> schedule must be 403."""
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    _override_enqueuer()
    # the admin creates the change; the read_only user tries to schedule it.
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    # switch to the read_only user
    await _login(api_client, "ro@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/schedule",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 403


async def test_read_only_forbidden_to_create(api_client, db_engine):
    """create is gated by CONFIG_PUSH -> read_only must get 403."""
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ro@x.io")
    r = await _create_change(api_client, tid, did)
    assert r.status_code == 403


async def test_read_only_forbidden_to_cancel(api_client, db_engine):
    """cancel is gated by CONFIG_PUSH -> read_only must get 403."""
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    created = await _create_change(api_client, tid, did)
    cid = created.json()["id"]
    await _login(api_client, "ro@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{cid}/cancel", headers=csrf_headers(api_client)
    )
    assert r.status_code == 403


async def test_create_requires_auth(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.post(
            f"/api/tenants/{tid}/devices/{did}/config/changes",
            json={"kind": "alias", "operation": "set", "target": "a", "payload": {}},
            headers={"X-OPNGMS-CSRF": "anon"},
        )
    assert r.status_code == 401


async def test_list_requires_auth(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(f"/api/tenants/{tid}/devices/{did}/config/changes")
    assert r.status_code == 401


async def test_create_requires_csrf(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/config/changes",
        json={"kind": "alias", "operation": "set", "target": "a", "payload": {}},
    )
    assert r.status_code == 403


async def test_preview_404_for_unknown_change(api_client, db_engine):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "ta@x.io")
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/config/changes/{uuid.uuid4()}/preview"
    )
    assert r.status_code == 404

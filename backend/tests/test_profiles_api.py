import json
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.queue import get_enqueuer
from app.main import app
from app.models.audit import AuditLog
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user

# A valid firewall_alias body the template engine accepts.
_VALID_BODY = {"name": "web", "type": "host", "content": ["1.2.3.4"]}


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


async def _seed_superadmin(db_engine):
    """Create a superadmin user (no tenant membership needed)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345", is_superadmin=True)
        await s.commit()


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


async def _seed_template(db_engine, *, kind="firewall_alias", name="web-allow", body=None):
    """Insert a global config_templates row via the owner engine (it's global, no tenant context)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tpl_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO config_templates "
                "(id, kind, name, description, body, version, created_by) "
                "VALUES (:id, :k, :n, '', CAST(:b AS jsonb), 1, :u)"
            ),
            {
                "id": tpl_id,
                "k": kind,
                "n": name,
                "b": json.dumps(body if body is not None else _VALID_BODY),
                "u": uuid.uuid4(),
            },
        )
        await s.commit()
    return tpl_id


def _override_enqueuer():
    """Override get_enqueuer with a fake recorder so no real Redis is touched."""
    calls: list = []

    async def _fake_enqueue(name, *args, defer_until=None):
        calls.append((name, args, defer_until))

    app.dependency_overrides[get_enqueuer] = lambda: _fake_enqueue
    return calls


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def test_superadmin_can_create_and_list_profile(api_client, db_engine):
    # superadmin bundles two library templates into a profile, then it lists with ordered template_ids
    await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    t1 = await _seed_template(db_engine, name="alias-a", body={"name": "a", "type": "host", "content": ["1.1.1.1"]})
    t2 = await _seed_template(db_engine, name="alias-b", body={"name": "b", "type": "host", "content": ["2.2.2.2"]})
    await _login(api_client, "sa@x.io")
    r = await api_client.post(
        "/api/profiles",
        json={"name": "baseline", "description": "two aliases", "template_ids": [str(t1), str(t2)]},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "baseline"
    assert body["template_ids"] == [str(t1), str(t2)]
    # any tenant user may LIST profiles (needed to apply)
    await _login(api_client, "ta@x.io")
    lst = await api_client.get("/api/profiles")
    assert lst.status_code == 200
    rows = [p for p in lst.json() if p["name"] == "baseline"]
    assert len(rows) == 1
    # members come back ordered by position
    assert rows[0]["template_ids"] == [str(t1), str(t2)]


async def test_non_superadmin_cannot_write_profiles(api_client, db_engine):
    # a tenant_admin (not superadmin) is forbidden to create/update/delete a profile
    await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    t1 = await _seed_template(db_engine, name="alias-a")
    # superadmin seeds a profile to PUT/DELETE against
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "p1", "template_ids": [str(t1)]},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    # now act as a non-superadmin tenant_admin
    await _login(api_client, "ta@x.io")
    rp = await api_client.post(
        "/api/profiles",
        json={"name": "p2", "template_ids": [str(t1)]},
        headers=csrf_headers(api_client),
    )
    assert rp.status_code == 403
    ru = await api_client.put(
        f"/api/profiles/{pid}",
        json={"name": "renamed"},
        headers=csrf_headers(api_client),
    )
    assert ru.status_code == 403
    rd = await api_client.delete(f"/api/profiles/{pid}", headers=csrf_headers(api_client))
    assert rd.status_code == 403


async def test_apply_profile_fans_out_two_jobs(api_client, db_engine):
    # superadmin makes a 2-template profile; a tenant operator applies it -> TWO config_change jobs enqueued
    tid = await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    did = await _insert_device(db_engine, tid)
    t1 = await _seed_template(db_engine, name="alias-a", body={"name": "a", "type": "host", "content": ["1.1.1.1"]})
    t2 = await _seed_template(db_engine, name="alias-b", body={"name": "b", "type": "host", "content": ["2.2.2.2"]})
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "baseline", "template_ids": [str(t1), str(t2)]},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/profiles/{pid}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code in (200, 201)
    body = r.json()
    assert body["status"] == "scheduled"
    assert len(body["change_ids"]) == 2
    # exactly two apply_config_change jobs, one per member, after commit
    assert len(calls) == 2
    assert all(name == "apply_config_change" for name, _args, _defer in calls)
    enqueued_ids = {args[0] for _name, args, _defer in calls}
    assert enqueued_ids == set(body["change_ids"])
    # audit row records the fan-out count
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "profile.apply"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id == tid
    assert rows[0].details.get("count") == 2


async def test_apply_empty_profile_is_400(api_client, db_engine):
    # a profile with no members -> apply rejected 400, nothing enqueued
    tid = await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "empty", "template_ids": []},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/profiles/{pid}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 400
    assert len(calls) == 0


async def test_preview_profile_returns_ordered_member_previews(api_client, db_engine):
    # superadmin creates a profile with TWO templates; a tenant operator previews it and gets
    # an ordered list of TemplatePreviewOut items, one per member, in member order.
    tid = await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    did = await _insert_device(db_engine, tid)
    t1 = await _seed_template(
        db_engine,
        name="alias-a",
        body={"name": "a", "type": "host", "content": ["1.1.1.1"]},
    )
    t2 = await _seed_template(
        db_engine,
        name="alias-b",
        body={"name": "b", "type": "host", "content": ["2.2.2.2"]},
    )
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "preview-baseline", "template_ids": [str(t1), str(t2)]},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/profiles/{pid}/preview",
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    # both items carry the expected operation/kind
    assert all(item["operation"] == "set" for item in items)
    assert all(item["kind"] == "alias" for item in items)
    # ORDER is preserved: first item corresponds to template t1 ("a"), second to t2 ("b")
    assert items[0]["new"]["name"] == "a"
    assert items[0]["new"]["content"] == ["1.1.1.1"]
    assert items[1]["new"]["name"] == "b"
    assert items[1]["new"]["content"] == ["2.2.2.2"]


async def test_preview_empty_profile_is_400(api_client, db_engine):
    # a profile with no members -> preview returns 400, nothing is computed
    tid = await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    did = await _insert_device(db_engine, tid)
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "empty-preview", "template_ids": []},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/profiles/{pid}/preview",
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 400


async def test_put_profile_replaces_member_set(api_client, db_engine):
    # superadmin creates profile [A, B], then PUTs [C, A]; the stored set becomes [C, A] (B gone,
    # C inserted, order preserved).
    await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    tA = await _seed_template(
        db_engine,
        name="alias-A",
        body={"name": "A", "type": "host", "content": ["10.0.0.1"]},
    )
    tB = await _seed_template(
        db_engine,
        name="alias-B",
        body={"name": "B", "type": "host", "content": ["10.0.0.2"]},
    )
    tC = await _seed_template(
        db_engine,
        name="alias-C",
        body={"name": "C", "type": "host", "content": ["10.0.0.3"]},
    )
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "replace-test", "template_ids": [str(tA), str(tB)]},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    # PUT replaces the member set with [C, A]
    r = await api_client.put(
        f"/api/profiles/{pid}",
        json={"template_ids": [str(tC), str(tA)]},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 200
    assert r.json()["template_ids"] == [str(tC), str(tA)]
    # verify via LIST as well (no duplicates/orphans)
    lst = await api_client.get("/api/profiles")
    assert lst.status_code == 200
    rows = [p for p in lst.json() if p["id"] == pid]
    assert len(rows) == 1
    assert rows[0]["template_ids"] == [str(tC), str(tA)]


async def test_apply_profile_cross_tenant_device_is_404(api_client, db_engine):
    # applying a profile to a device that belongs to another tenant -> 404 (device not found in this tenant)
    tid = await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    t1 = await _seed_template(db_engine, name="alias-a")
    await _login(api_client, "sa@x.io")
    created = await api_client.post(
        "/api/profiles",
        json={"name": "baseline", "template_ids": [str(t1)]},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    pid = created.json()["id"]
    # a device belonging to a DIFFERENT tenant
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other")
        await s.commit()
        other_tid = other.id
    foreign_did = await _insert_device(db_engine, other_tid, name="fw-other")
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{foreign_did}/profiles/{pid}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 404
    assert len(calls) == 0

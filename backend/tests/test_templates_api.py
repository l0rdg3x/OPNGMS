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


async def _seed_override(db_engine, tenant_id, template_id, body_patch):
    """Insert a per-tenant template_overrides row via the owner engine."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    oid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO template_overrides "
                "(id, template_id, tenant_id, body_patch) "
                "VALUES (:id, :tpl, :t, CAST(:p AS jsonb))"
            ),
            {"id": oid, "tpl": template_id, "t": tenant_id, "p": json.dumps(body_patch)},
        )
        await s.commit()
    return oid


def _override_enqueuer():
    """Override get_enqueuer with a fake recorder so no real Redis is touched."""
    calls: list = []

    async def _fake_enqueue(name, *args, defer_until=None):
        calls.append((name, args, defer_until))

    app.dependency_overrides[get_enqueuer] = lambda: _fake_enqueue
    return calls


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def test_superadmin_can_create_and_list_template(api_client, db_engine):
    # superadmin creates a firewall_alias template in the global library
    await _seed_members(db_engine)
    await _seed_superadmin(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post(
        "/api/templates",
        json={
            "kind": "firewall_alias",
            "name": "web-allow",
            "body": {"name": "web_allow", "type": "host", "content": ["1.2.3.4"]},
        },
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 201
    assert r.json()["name"] == "web-allow"
    # any tenant user can LIST the library (needed to apply)
    await _login(api_client, "ta@x.io")
    lst = await api_client.get("/api/templates")
    assert lst.status_code == 200
    assert any(t["name"] == "web-allow" for t in lst.json())


async def test_non_superadmin_cannot_write_library(api_client, db_engine):
    # a tenant_admin (not superadmin) is forbidden to create a library template
    await _seed_members(db_engine)
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        "/api/templates",
        json={
            "kind": "firewall_alias",
            "name": "x",
            "body": {"name": "x", "type": "host", "content": ["1.1.1.1"]},
        },
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 403


async def test_apply_template_enqueues_config_change(api_client, db_engine):
    # superadmin makes a template; a tenant operator applies it to their device -> a config_change is enqueued
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    template_id = await _seed_template(db_engine)
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/templates/{template_id}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code in (200, 201)
    assert r.json()["status"] == "scheduled"
    assert len(calls) == 1
    name, args, defer_until = calls[0]
    assert name == "apply_config_change"
    assert defer_until is None
    # the enqueued arg is the new change's id
    assert args == (r.json()["change_id"],)


async def test_apply_template_writes_audit_row(api_client, db_engine):
    # applying a template records a template.apply audit row for the new config_change
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    template_id = await _seed_template(db_engine)
    _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/templates/{template_id}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code in (200, 201)
    change_id = r.json()["change_id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "template.apply")
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.target_type == "config_change"
    assert row.target_id == change_id
    assert row.tenant_id == tid
    assert row.details.get("template_id") == str(template_id)


async def test_create_template_writes_audit_row(api_client, db_engine):
    # superadmin creating a library template records a template.create audit row (tenant_id=None)
    await _seed_superadmin(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post(
        "/api/templates",
        json={
            "kind": "firewall_alias",
            "name": "audited",
            "body": {"name": "audited", "type": "host", "content": ["1.2.3.4"]},
        },
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 201
    tpl_id = r.json()["id"]
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "template.create")
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.target_type == "config_template"
    assert row.target_id == tpl_id
    assert row.tenant_id is None
    assert row.details.get("name") == "audited"


async def test_apply_invalid_effective_body_is_422(api_client, db_engine):
    # an override that empties content -> invalid effective body -> 422, no enqueue
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    template_id = await _seed_template(db_engine)
    # override the content with an empty list -> validate_alias_body rejects it
    await _seed_override(db_engine, tid, template_id, {"content": []})
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/templates/{template_id}/apply",
        json={"scheduled_at": None},
        headers=csrf_headers(api_client),
    )
    assert r.status_code == 422
    assert len(calls) == 0

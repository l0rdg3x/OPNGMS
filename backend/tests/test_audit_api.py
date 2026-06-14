from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.audit import AuditLog
from tests.factories import make_membership, make_tenant, make_user


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_superadmin(session_factory, *, email="sa@test.com"):
    async with session_factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure", is_superadmin=True)
        await s.commit()
        return user.id


async def _seed_tenant_admin(session_factory, *, email="ta@test.com"):
    """A non-superadmin user who is tenant_admin on a fresh tenant. Returns (user_id, tenant_id)."""
    async with session_factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure", is_superadmin=False)
        tenant = await make_tenant(s, slug=email.split("@")[0])
        await make_membership(s, user_id=user.id, tenant_id=tenant.id, role="tenant_admin")
        await s.commit()
        return user.id, tenant.id


async def _login(client, email="sa@test.com"):
    r = await client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200, r.text


async def _seed_audit_rows(session_factory, *, tenant_a, tenant_b, actor_id):
    """Seed: row for tenant A (with actor), row for tenant B (with actor), and a NULL-actor /
    NULL-tenant row. Returns the three ids and their timestamps."""
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    rows = [
        AuditLog(
            ts=base,
            actor_user_id=actor_id,
            tenant_id=tenant_a,
            action="tenant.manage",
            target_type="tenant",
            target_id=str(tenant_a),
            ip="10.0.0.1",
            details={"k": "v"},
        ),
        AuditLog(
            ts=base + timedelta(minutes=1),
            actor_user_id=actor_id,
            tenant_id=tenant_b,
            action="device.write",
            target_type="device",
            target_id="dev-b",
            ip="10.0.0.2",
            details={},
        ),
        AuditLog(
            ts=base + timedelta(minutes=2),
            actor_user_id=None,
            tenant_id=None,
            action="setup.bootstrap",
            target_type="user",
            target_id="someone",
            ip=None,
            details={"email": "first@admin.io"},
        ),
    ]
    async with session_factory() as s:
        for r in rows:
            s.add(r)
        await s.commit()
        return [r.id for r in rows], base


# --------------------------------------------------------------------------- authz

async def test_unauthenticated_gets_401(api_client):
    r = await api_client.get("/api/admin/audit")
    assert r.status_code == 401
    r = await api_client.get("/api/admin/audit/export.csv")
    assert r.status_code == 401


async def test_tenant_admin_forbidden_on_list_and_csv(api_client, session_factory):
    await _seed_tenant_admin(session_factory)
    await _login(api_client, email="ta@test.com")
    r = await api_client.get("/api/admin/audit")
    assert r.status_code == 403
    r = await api_client.get("/api/admin/audit/export.csv")
    assert r.status_code == 403


# ---------------------------------------------------------------- cross-tenant read

async def test_superadmin_sees_all_tenants_including_null(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = (await _make_tenant_id(session_factory, "alpha"))
    tenant_b = (await _make_tenant_id(session_factory, "beta"))
    ids, _ = await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)  # the login itself records one auth.login audit row
    r = await api_client.get("/api/admin/audit")
    assert r.status_code == 200
    body = r.json()
    # The 3 seeded rows + the auth.login row from the test's own login.
    assert body["total"] == 4
    returned = {i["id"] for i in body["items"]}
    assert {str(x) for x in ids}.issubset(returned)
    actions = {i["action"] for i in body["items"]}
    assert {"tenant.manage", "device.write", "setup.bootstrap", "auth.login"} == actions


async def test_cross_tenant_read_under_real_app_role(app_role_api_client, session_factory):
    """The non-RLS audit read must work under the production opngms_app role (RLS active), not just
    the owner: prove the superadmin genuinely sees rows for tenants they have no membership on."""
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(app_role_api_client)
    r = await app_role_api_client.get("/api/admin/audit")
    assert r.status_code == 200
    body = r.json()
    # 3 seeded (across two tenants + a NULL row) + the auth.login row. The point: under RLS the
    # superadmin still sees rows for tenants they have no membership on.
    assert body["total"] == 4
    tenants = {i["tenant_id"] for i in body["items"]}
    assert str(tenant_a) in tenants and str(tenant_b) in tenants and None in tenants


# ---------------------------------------------------------------- enrichment

async def test_enrichment_email_and_tenant_name_with_null_cases(api_client, session_factory):
    actor = await _seed_superadmin(session_factory, email="sa@test.com")
    tenant_a = await _make_tenant_id(session_factory, "alpha", name="Alpha Inc")
    tenant_b = await _make_tenant_id(session_factory, "beta", name="Beta LLC")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit")
    body = r.json()
    by_action = {i["action"]: i for i in body["items"]}
    # actor present -> email resolved; tenant present -> name resolved
    assert by_action["tenant.manage"]["actor_email"] == "sa@test.com"
    assert by_action["tenant.manage"]["tenant_name"] == "Alpha Inc"
    assert by_action["device.write"]["tenant_name"] == "Beta LLC"
    # NULL actor / NULL tenant -> both None
    assert by_action["setup.bootstrap"]["actor_email"] is None
    assert by_action["setup.bootstrap"]["tenant_name"] is None
    assert by_action["setup.bootstrap"]["actor_user_id"] is None


# ---------------------------------------------------------------- filters + pagination

async def test_filter_by_action(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit", params={"action": "device.write"})
    body = r.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["action"] == "device.write"


async def test_filter_by_tenant(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit", params={"tenant_id": str(tenant_a)})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["tenant_id"] == str(tenant_a)


async def test_filter_by_date_range(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    _, base = await _seed_audit_rows(
        session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor
    )
    await _login(api_client)
    # frm inclusive of the 2nd row, to excludes the 3rd row -> only the middle (device.write).
    frm = (base + timedelta(seconds=30)).isoformat()
    to = (base + timedelta(seconds=90)).isoformat()
    r = await api_client.get("/api/admin/audit", params={"frm": frm, "to": to})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["action"] == "device.write"


async def test_pagination_limit_offset_total(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)  # adds one auth.login row -> 4 total
    r = await api_client.get("/api/admin/audit", params={"limit": 2, "offset": 0})
    body = r.json()
    assert body["total"] == 4  # total ignores pagination
    assert len(body["items"]) == 2
    first_page_ids = [i["id"] for i in body["items"]]
    r2 = await api_client.get("/api/admin/audit", params={"limit": 2, "offset": 2})
    body2 = r2.json()
    assert body2["total"] == 4
    assert len(body2["items"]) == 2
    assert all(i["id"] not in first_page_ids for i in body2["items"])


async def test_limit_is_capped_at_200(api_client, session_factory):
    await _seed_superadmin(session_factory)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit", params={"limit": 5000})
    assert r.status_code == 422  # Query(le=200) rejects out-of-range limits


# ---------------------------------------------------------------- csv

async def test_csv_export_header_and_rows(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha", name="Alpha Inc")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="audit.csv"'
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert lines[0].startswith("ts,actor_user_id,actor_email,tenant_id,tenant_name,action,")
    # 1 header + 3 seeded rows + the auth.login row from this test's login.
    assert len(lines) == 5
    assert any("setup.bootstrap" in ln for ln in lines[1:])
    assert any("sa@test.com" in ln for ln in lines[1:])


async def test_csv_export_respects_action_filter(api_client, session_factory):
    actor = await _seed_superadmin(session_factory)
    tenant_a = await _make_tenant_id(session_factory, "alpha")
    tenant_b = await _make_tenant_id(session_factory, "beta")
    await _seed_audit_rows(session_factory, tenant_a=tenant_a, tenant_b=tenant_b, actor_id=actor)
    await _login(api_client)
    r = await api_client.get("/api/admin/audit/export.csv", params={"action": "device.write"})
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    assert len(lines) == 2  # header + 1 row
    assert "device.write" in lines[1]


# --------------------------------------------------------------------------- helpers

async def _make_tenant_id(session_factory, slug, *, name="Tenant"):
    async with session_factory() as s:
        t = await make_tenant(s, slug=slug, name=name)
        await s.commit()
        return t.id

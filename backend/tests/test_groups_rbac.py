"""Group-based RBAC: effective-role resolution, group-granted API access, and the admin API."""
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.access import resolve_effective_role, tenants_for_user
from app.core.rls import TENANT_TABLES
from app.models.group import Group, GroupGrant, GroupMember
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_tenant, make_user


async def _mk_group(s, name, user_ids, grants):
    """grants: list of (all_tenants, tenant_id|None, role)."""
    g = Group(name=name)
    s.add(g)
    await s.flush()
    for uid in user_ids:
        s.add(GroupMember(group_id=g.id, user_id=uid))
    for all_t, tid, role in grants:
        s.add(GroupGrant(group_id=g.id, all_tenants=all_t, tenant_id=tid, role=role))
    await s.flush()
    return g


def test_group_tables_are_org_level_not_rls():
    """Structural guard: group tables are org-level (no RLS) — like memberships/users."""
    for table in ("groups", "group_members", "group_grants"):
        assert table not in TENANT_TABLES


# ── resolution unit tests ──────────────────────────────────────────────────

async def test_resolve_specific_tenant_grant(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="a")
        u = await make_user(s, email="u@x.io")
        await _mk_group(s, "ops", [u.id], [(False, t.id, "operator")])
        await s.commit()
        assert await resolve_effective_role(s, user=u, tenant_id=t.id) == "operator"


async def test_resolve_wildcard_covers_any_tenant(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        u = await make_user(s, email="u@x.io")
        await _mk_group(s, "staff", [u.id], [(True, None, "tenant_admin")])
        await s.commit()
        assert await resolve_effective_role(s, user=u, tenant_id=a.id) == "tenant_admin"
        assert await resolve_effective_role(s, user=u, tenant_id=b.id) == "tenant_admin"


async def test_resolve_highest_of_membership_and_group(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="a")
        u = await make_user(s, email="u@x.io")
        await make_membership(s, user_id=u.id, tenant_id=t.id, role="read_only")
        await _mk_group(s, "ops", [u.id], [(False, t.id, "operator")])
        await s.commit()
        # operator (group) beats read_only (direct membership)
        assert await resolve_effective_role(s, user=u, tenant_id=t.id) == "operator"


async def test_resolve_none_without_any_access(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="a")
        u = await make_user(s, email="u@x.io")
        await s.commit()
        assert await resolve_effective_role(s, user=u, tenant_id=t.id) is None


async def test_tenants_for_user_union(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        c = await make_tenant(s, slug="c")
        u = await make_user(s, email="u@x.io")
        await make_membership(s, user_id=u.id, tenant_id=a.id, role="read_only")
        await _mk_group(s, "ops", [u.id], [(False, b.id, "operator"), (False, a.id, "tenant_admin")])
        await s.commit()
        result = await tenants_for_user(s, u)
    assert result[a.id] == "tenant_admin"  # highest of read_only(direct) + tenant_admin(group)
    assert result[b.id] == "operator"
    assert c.id not in result


async def test_tenants_for_user_wildcard_lists_all(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        u = await make_user(s, email="u@x.io")
        await _mk_group(s, "staff", [u.id], [(True, None, "operator")])
        await s.commit()
        result = await tenants_for_user(s, u)
    assert result[a.id] == "operator" and result[b.id] == "operator"


# ── API integration ────────────────────────────────────────────────────────

async def _seed_login_user(db_engine, *, email, password="pw12345-secure"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email=email, password=password)
        await s.commit()
        return t.id, u.id


async def test_group_member_reaches_tenant_via_grant(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="g@x.io", password="pw12345-secure")
        # No direct membership — access is only via the group grant.
        await _mk_group(s, "ops", [u.id], [(True, None, "operator")])
        await s.commit()
        tid = t.id
    await api_client.post("/api/login", json={"email": "g@x.io", "password": "pw12345-secure"})
    resp = await api_client.get(f"/api/tenants/{tid}/devices")
    assert resp.status_code == 200


async def test_wildcard_member_only_sees_path_tenant_rows(api_client, db_engine):
    """A wildcard grant widens tenant ENTRY, not RLS/scope: entering tenant B returns ONLY B's rows.

    Seeds a device in tenant A and tenant B; a user with an all-tenants grant requests tenant B's
    devices and must see B's device only — never A's (the endpoint is pinned to the path tenant)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="ta")
        b = await make_tenant(s, slug="tb")
        u = await make_user(s, email="w@x.io", password="pw12345-secure")
        await _mk_group(s, "staff", [u.id], [(True, None, "operator")])
        for tid, name in ((a.id, "dev-A"), (b.id, "dev-B")):
            await s.execute(
                text(
                    "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                    "verify_tls, status, tags) VALUES "
                    "(:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
                ),
                {"id": uuid.uuid4(), "t": tid, "n": name},
            )
        await s.commit()
        tid_b = b.id
    await api_client.post("/api/login", json={"email": "w@x.io", "password": "pw12345-secure"})
    resp = await api_client.get(f"/api/tenants/{tid_b}/devices")
    assert resp.status_code == 200
    names = {d["name"] for d in resp.json()}
    assert names == {"dev-B"}  # tenant A's device must NOT leak in


async def test_user_without_membership_or_grant_403(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await make_user(s, email="nobody@x.io", password="pw12345-secure")
        await s.commit()
        tid = t.id
    await api_client.post("/api/login", json={"email": "nobody@x.io", "password": "pw12345-secure"})
    resp = await api_client.get(f"/api/tenants/{tid}/devices")
    assert resp.status_code == 403


async def test_read_only_grant_cannot_write(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await _mk_group(s, "viewers", [u.id], [(True, None, "read_only")])
        await s.commit()
        tid = t.id
    await api_client.post("/api/login", json={"email": "ro@x.io", "password": "pw12345-secure"})
    # read_only may GET devices...
    assert (await api_client.get(f"/api/tenants/{tid}/devices")).status_code == 200
    # ...but not create one (DEVICE_WRITE not granted to read_only).
    denied = await api_client.post(
        f"/api/tenants/{tid}/devices",
        json={"name": "x", "base_url": "https://x", "api_key": "k", "api_secret": "s"},
        headers=csrf_headers(api_client),
    )
    assert denied.status_code == 403


# ── admin API (superadmin only) ─────────────────────────────────────────────

async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})


async def test_admin_group_crud_roundtrip(api_client, db_engine):
    await _login_superadmin(api_client)
    # create
    created = await api_client.post(
        "/api/groups", json={"name": "MSP Staff", "description": "all"},
        headers=csrf_headers(api_client),
    )
    assert created.status_code == 201
    gid = created.json()["id"]
    # add a wildcard tenant_admin grant
    grant = await api_client.post(
        f"/api/groups/{gid}/grants", json={"all_tenants": True, "role": "tenant_admin"},
        headers=csrf_headers(api_client),
    )
    assert grant.status_code == 201
    # list reflects it
    listed = await api_client.get("/api/groups")
    g = next(x for x in listed.json() if x["id"] == gid)
    assert g["grants"][0]["all_tenants"] is True and g["grants"][0]["role"] == "tenant_admin"


async def test_grant_rejects_non_tenant_role(api_client, db_engine):
    await _login_superadmin(api_client)
    gid = (await api_client.post(
        "/api/groups", json={"name": "G"}, headers=csrf_headers(api_client)
    )).json()["id"]
    # 'superadmin' / any org role must be rejected — groups grant tenant roles only.
    resp = await api_client.post(
        f"/api/groups/{gid}/grants", json={"all_tenants": True, "role": "superadmin"},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 422


async def test_grant_rejects_ambiguous_scope(api_client, db_engine):
    await _login_superadmin(api_client)
    gid = (await api_client.post(
        "/api/groups", json={"name": "G"}, headers=csrf_headers(api_client)
    )).json()["id"]
    # both all_tenants AND tenant_id set -> invalid
    resp = await api_client.post(
        f"/api/groups/{gid}/grants",
        json={"all_tenants": True, "tenant_id": str(uuid.uuid4()), "role": "operator"},
        headers=csrf_headers(api_client),
    )
    assert resp.status_code == 422


async def test_non_superadmin_cannot_manage_groups(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        u = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=u.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
    await api_client.post("/api/login", json={"email": "ta@x.io", "password": "pw12345-secure"})
    # tenant_admin (not superadmin) is denied the org-level group admin API.
    assert (await api_client.get("/api/groups")).status_code == 403
    assert (await api_client.post(
        "/api/groups", json={"name": "x"}, headers=csrf_headers(api_client)
    )).status_code == 403

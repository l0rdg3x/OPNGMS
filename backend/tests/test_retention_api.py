"""API tests for /api/tenants/{tid}/retention: GET defaults+overrides, PUT authz/validation, RLS."""
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user


async def _seed(db_engine):
    """One tenant with a tenant_admin, an operator, and a read_only user. Returns the tenant id."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        op = await make_user(s, email="op@x.io", password="pw12345-secure")
        await make_membership(s, user_id=op.id, tenant_id=t.id, role="operator")
        ro = await make_user(s, email="ro@x.io", password="pw12345-secure")
        await make_membership(s, user_id=ro.id, tenant_id=t.id, role="read_only")
        await s.commit()
        return t.id


async def _login(client, email):
    r = await client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200


def _csrf(client):
    return {"X-OPNGMS-CSRF": client.cookies.get("opngms_csrf")}


async def test_get_returns_empty_overrides_and_global_defaults(api_client, db_engine):
    tid = await _seed(db_engine)
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/retention")
    assert r.status_code == 200
    body = r.json()
    assert body["overrides"] == {}
    # defaults reflect the runtime registry (env/code defaults for the three stores)
    assert body["defaults"] == {"perimeter": 30, "events": 90, "metrics": 30}


async def test_tenant_admin_can_put_and_get_overrides(api_client, db_engine):
    tid = await _seed(db_engine)
    await _login(api_client, "ta@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/retention",
                             json={"values": {"perimeter": 7, "events": 14}}, headers=_csrf(api_client))
    assert r.status_code == 200
    assert r.json()["overrides"] == {"perimeter": 7, "events": 14}
    # persisted: a fresh GET sees them, defaults unchanged
    r = await api_client.get(f"/api/tenants/{tid}/retention")
    assert r.json()["overrides"] == {"perimeter": 7, "events": 14}
    # clearing a key (null) removes it (back to inherit)
    r = await api_client.put(f"/api/tenants/{tid}/retention",
                             json={"values": {"perimeter": None}}, headers=_csrf(api_client))
    assert r.status_code == 200 and r.json()["overrides"] == {"events": 14}


async def test_operator_and_read_only_cannot_put(api_client, db_engine):
    tid = await _seed(db_engine)
    for email in ("op@x.io", "ro@x.io"):
        await _login(api_client, email)
        r = await api_client.put(f"/api/tenants/{tid}/retention",
                                 json={"values": {"perimeter": 7}}, headers=_csrf(api_client))
        assert r.status_code == 403, email


async def test_unknown_store_key_is_422(api_client, db_engine):
    tid = await _seed(db_engine)
    await _login(api_client, "ta@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/retention",
                             json={"values": {"log_lake": 10}}, headers=_csrf(api_client))
    assert r.status_code == 422


async def test_out_of_range_value_is_422(api_client, db_engine):
    tid = await _seed(db_engine)
    await _login(api_client, "ta@x.io")
    for bad in (0, 3651, -1):
        r = await api_client.put(f"/api/tenants/{tid}/retention",
                                 json={"values": {"perimeter": bad}}, headers=_csrf(api_client))
        assert r.status_code == 422, bad


async def test_cross_tenant_access_denied(app_role_api_client, db_engine):
    """A tenant_admin of tenant A has no membership on tenant B -> 403 (no leak of B's overrides).

    Uses the app-role client so the request runs as the non-superuser opngms_app role (RLS active),
    proving the deny is real authorization, not an owner-only application filter.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await make_tenant(s, slug="a")
        tb = await make_tenant(s, slug="b")
        admin_a = await make_user(s, email="a-admin@x.io", password="pw12345-secure")
        await make_membership(s, user_id=admin_a.id, tenant_id=ta.id, role="tenant_admin")
        await s.commit()
        tb_id = tb.id
    await _login(app_role_api_client, "a-admin@x.io")
    # GET another tenant's retention -> denied
    r = await app_role_api_client.get(f"/api/tenants/{tb_id}/retention")
    assert r.status_code == 403
    # PUT another tenant's retention -> denied
    r = await app_role_api_client.put(f"/api/tenants/{tb_id}/retention",
                                      json={"values": {"perimeter": 1}}, headers=_csrf(app_role_api_client))
    assert r.status_code == 403

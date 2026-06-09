from app.services.onboarding import ProbeResult, get_prober

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _setup_two_tenants(app_role_api_client, db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.main import app
    from tests.factories import make_tenant

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    # superadmin via /api/setup (can access all tenants)
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )

    async def _fake(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await app_role_api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return ta, tb


async def test_device_created_in_tenant_a_not_visible_in_tenant_b(app_role_api_client, db_engine):
    ta, tb = await _setup_two_tenants(app_role_api_client, db_engine)
    created = await app_role_api_client.post(
        f"/api/tenants/{ta}/devices",
        json={"name": "fw-a", "base_url": "https://a", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert created.status_code == 201
    la = await app_role_api_client.get(f"/api/tenants/{ta}/devices")
    assert [d["name"] for d in la.json()] == ["fw-a"]
    lb = await app_role_api_client.get(f"/api/tenants/{tb}/devices")
    assert lb.json() == []

import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.main import app
from app.services.onboarding import ProbeResult, get_prober
from tests.conftest import csrf_headers
from tests.factories import make_tenant


async def _setup(app_role_api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )

    async def _fake(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await app_role_api_client.post(
        "/api/login", json={"email": "sa@x.io", "password": "pw12345"}
    )
    return ta, tb


async def _make_device(app_role_api_client, tid, name):
    r = await app_role_api_client.post(
        f"/api/tenants/{tid}/devices",
        json={"name": name, "base_url": f"https://{name}", "api_key": "k", "api_secret": "s"},
        headers=csrf_headers(app_role_api_client),
    )
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def test_metrics_and_alerts_isolated_via_api(app_role_api_client, db_engine):
    """End-to-end monitoring isolation, across three levels of proof:

    (a) Behavior via the API (defense-in-depth: the endpoints/repositories apply
        an explicit application filter ``WHERE tenant_id = <tenant from the path>`` *and*
        RLS runs underneath). A sees its own data, B sees its own.
    (b) The real ``opngms_app`` reads its own Timescale hypertable chunks through
        the API (grant propagation): positive assertion ``value == 11.0``.
    (c) It is RLS — not the application filter — that isolates cross-tenant: at the end of
        the test a RAW query *without* ``WHERE tenant_id``, run as the real ``opngms_app``
        role with context on tenant A, sees only A's rows. Without the application filter
        the only remaining defense is RLS, so this assertion would fail if RLS were
        disabled (it is not tautological).

    The pure SQL-level RLS proof is also in
    ``tests/test_rls_isolation.py::test_metrics_alerts_isolated_cross_tenant``.
    """
    ta, tb = await _setup(app_role_api_client, db_engine)
    dev_a = await _make_device(app_role_api_client, ta, "fw-a")
    dev_b = await _make_device(app_role_api_client, tb, "fw-b")

    # inject data as owner (bypasses RLS) for both
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        for tid, did, val in ((ta, dev_a, 11.0), (tb, dev_b, 22.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": did, "tid": tid, "v": val},
            )
            await s.execute(
                text(
                    "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, details) "
                    "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
                ),
                {"id": uuid.uuid4(), "tid": tid, "did": did},
            )
        await s.commit()

    # real opngms_app reads its own Timescale hypertable chunks through the API:
    # POSITIVE assertion -> proves grant propagation to the chunks (it is not tautological).
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/metrics", params={"metric": "cpu.load"}
    )
    assert ra.json()["points"][0]["value"] == 11.0
    # B's data on B's device, queried in A's context -> no point.
    # NB: here the endpoint's application filter WHERE tenant_id already isolates, so this
    # negative assertion does NOT distinguish "RLS active" from "application filter only": it is a
    # behavior test (defense-in-depth). The proof that it is RLS that isolates is
    # the RAW query at the end of the test.
    cross = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_b}/metrics", params={"metric": "cpu.load"}
    )
    assert cross.json()["points"] == []

    aa = await app_role_api_client.get(f"/api/tenants/{ta}/alerts")
    assert [x["device_id"] for x in aa.json()] == [str(dev_a)]
    ab = await app_role_api_client.get(f"/api/tenants/{tb}/alerts")
    assert [x["device_id"] for x in ab.json()] == [str(dev_b)]

    ha = await app_role_api_client.get(f"/api/tenants/{ta}/health")
    assert ha.json()["total_devices"] == 1
    assert ha.json()["active_alerts"] == 1

    # Proof that it is RLS (not the application filter) that isolates cross-tenant.
    # DIRECT session as the real opngms_app role (NOT via the API, NOT as owner),
    # context on tenant A, RAW query WITHOUT WHERE tenant_id: the only remaining defense
    # is RLS. It must see ONLY A's rows -> it would fail if RLS were off.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, ta)
            vals = (
                await s.execute(text("SELECT value FROM metrics ORDER BY value"))
            ).scalars().all()
            assert vals == [11.0]  # NOT [11.0, 22.0]: without the application filter it is RLS that excludes B
            n_alerts = (
                await s.execute(text("SELECT count(*) FROM alerts"))
            ).scalar_one()
            assert n_alerts == 1  # only A's alert
    finally:
        await engine.dispose()

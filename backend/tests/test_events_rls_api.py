import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from tests.factories import make_tenant


async def _setup(app_role_api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"}
    )
    await app_role_api_client.post(
        "/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"}
    )
    return ta, tb


async def test_events_isolated_via_api(app_role_api_client, db_engine):
    """End-to-end events isolation, across three levels of proof:

    (a) Behavior via the API (defense-in-depth: the repository applies an explicit
        application filter ``WHERE tenant_id = <tenant from the path>`` *and* RLS runs
        underneath). A sees only its own events.
    (b) The real ``opngms_app`` reads its own Timescale hypertable chunks through the
        API (grant propagation): positive assertion on A's event names.
    (c) It is RLS — not the application filter — that isolates cross-tenant: at the end
        a RAW query *without* ``WHERE tenant_id``, run as the real ``opngms_app`` role with
        context on tenant A, sees only A's rows. Without the application filter the only
        remaining defense is RLS, so this assertion would fail if RLS were disabled
        (it is not tautological).
    """
    ta, tb = await _setup(app_role_api_client, db_engine)
    dev_a, dev_b = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)

    # inject events as owner (bypasses RLS) for both tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        seed = [
            (ta, dev_a, "ids", "ka", "A-EVENT"),
            (tb, dev_b, "ids", "kb", "B-EVENT"),
        ]
        for tid, did, src, key, name in seed:
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name) "
                    "VALUES (:t, :d, :src, :k, :tid, :name)"
                ),
                {"t": base, "d": did, "src": src, "k": key, "tid": tid, "name": name},
            )
        await s.commit()

    # real opngms_app reads its own Timescale hypertable chunks through the API:
    # POSITIVE assertion -> proves grant propagation to the chunks (not tautological).
    ra = await app_role_api_client.get(f"/api/tenants/{ta}/events")
    assert ra.status_code == 200
    assert [e["name"] for e in ra.json()["items"]] == ["A-EVENT"]

    rb = await app_role_api_client.get(f"/api/tenants/{tb}/events")
    assert rb.status_code == 200
    assert [e["name"] for e in rb.json()["items"]] == ["B-EVENT"]

    # Cross-tenant via API: B's events queried in A's context -> none.
    # NB: here the repository's application filter WHERE tenant_id already isolates, so this
    # negative assertion does NOT distinguish "RLS active" from "application filter only": it is a
    # behavior test (defense-in-depth). The proof that it is RLS that isolates is the RAW query below.
    assert "B-EVENT" not in [e["name"] for e in ra.json()["items"]]

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
            names = (
                await s.execute(text("SELECT name FROM events ORDER BY name"))
            ).scalars().all()
            # NOT ["A-EVENT", "B-EVENT"]: without the application filter it is RLS that excludes B.
            assert names == ["A-EVENT"]
    finally:
        await engine.dispose()

import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.core.rls import TENANT_TABLES
from tests.factories import make_tenant

CSRF = {"X-OPNGMS-CSRF": "1"}


def test_config_changes_in_tenant_tables():
    """Static guard: the table must be RLS-managed (in TENANT_TABLES)."""
    assert "config_changes" in TENANT_TABLES


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
    await app_role_api_client.post(
        "/api/login", json={"email": "sa@x.io", "password": "pw12345"}
    )
    return ta, tb


async def _seed_device(s, tenant_id, name):
    """As owner (RLS-bypassing): insert a device. Returns device_id."""
    did = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO devices "
            "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
        ),
        {"id": did, "t": tenant_id, "n": name},
    )
    return did


async def _seed_change(s, tenant_id, device_id, target):
    """As owner (RLS-bypassing): insert a draft config_change. Returns change_id."""
    cid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO config_changes "
            "(id, tenant_id, device_id, created_by, kind, operation, target, payload, baseline_hash, status) "
            "VALUES (:id, :t, :d, :u, 'alias', 'set', :tg, '{}'::jsonb, 'h', 'draft')"
        ),
        {"id": cid, "t": tenant_id, "d": device_id, "u": uuid.uuid4(), "tg": target},
    )
    return cid


async def test_create_change_cross_tenant_device_is_404(app_role_api_client, db_engine):
    """Cross-tenant authorization gap closed: A cannot create a config_change for B's device.

    The apply job runs in the worker as the DB owner (RLS bypassed) and loads the device
    by id, so create MUST refuse a device the caller cannot see. Tenant A POSTs a change
    targeting B's device under A's tenant path: the RLS-scoped device lookup returns None
    -> 404, and NO config_changes row is created for B's device.
    """
    ta, tb = await _setup(app_role_api_client, db_engine)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev_a = await _seed_device(s, ta, "fw-a")
        dev_b = await _seed_device(s, tb, "fw-b")
        await s.commit()

    # A (under its own tenant path) tries to create a change for B's device -> 404.
    r = await app_role_api_client.post(
        f"/api/tenants/{ta}/devices/{dev_b}/config/changes",
        json={"kind": "alias", "operation": "set", "target": "alias-x", "payload": {}},
        headers=CSRF,
    )
    assert r.status_code == 404

    # Assert no config_changes row exists for B's device (as owner, bypassing RLS).
    async with factory() as s:
        count = (
            await s.execute(
                text("SELECT count(*) FROM config_changes WHERE device_id = :d"),
                {"d": dev_b},
            )
        ).scalar_one()
        assert count == 0


async def test_config_changes_isolated_via_api(app_role_api_client, db_engine):
    """End-to-end config-change isolation across three levels of proof:

    (a) Behavior via the API: A sees only its own change in the list; B's change
        is not visible.
    (b) Cross-tenant get/preview of B's change from A's path is a 404 (no leak).
    (c) It is RLS — not the application filter — that isolates cross-tenant: a RAW
        query *without* ``WHERE tenant_id``, run as the real ``opngms_app`` role with
        context on tenant A, sees only A's rows. Without the application filter the
        only remaining defense is RLS, so this assertion would fail if RLS were off.
    """
    ta, tb = await _setup(app_role_api_client, db_engine)

    # Seed devices + changes as owner (bypasses RLS) for both tenants.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev_a = await _seed_device(s, ta, "fw-a")
        dev_b = await _seed_device(s, tb, "fw-b")
        change_a = await _seed_change(s, ta, dev_a, "alias-a")
        change_b = await _seed_change(s, tb, dev_b, "alias-b")
        await s.commit()

    # (a) real opngms_app lists its own changes through the API.
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/config/changes"
    )
    assert ra.status_code == 200
    assert [c["target"] for c in ra.json()] == ["alias-a"]
    assert "alias-b" not in ra.text

    # (b) A cannot preview B's change via A's path -> 404 (no leak).
    rb = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/config/changes/{change_b}/preview"
    )
    assert rb.status_code == 404
    # and B's change is not reachable even by its own device id under A's tenant path
    rb2 = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_b}/config/changes/{change_b}/preview"
    )
    assert rb2.status_code == 404

    # (c) Proof that it is RLS (not the application filter) that isolates cross-tenant.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        raw_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with raw_factory() as s:
            await set_tenant_context(s, ta)
            targets = (
                await s.execute(
                    text("SELECT target FROM config_changes ORDER BY target")
                )
            ).scalars().all()
            # NOT ["alias-a", "alias-b"]: without the application filter it is RLS
            # that excludes B.
            assert targets == ["alias-a"]
        # And with no tenant context set at all -> RLS hides everything.
        async with raw_factory() as s2:
            none = (
                await s2.execute(text("SELECT target FROM config_changes"))
            ).scalars().all()
            assert none == []
    finally:
        await engine.dispose()

import gzip
import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.core.rls import TENANT_TABLES
from tests.factories import make_tenant


def test_config_snapshots_in_tenant_tables():
    """Static guard: the table must be RLS-managed (in TENANT_TABLES)."""
    assert "config_snapshots" in TENANT_TABLES


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


async def _seed_device_and_snapshot(s, tenant_id, name, canonical_hash):
    """As owner (RLS-bypassing): insert a device + one encrypted snapshot. Returns device_id."""
    did = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO devices "
            "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
        ),
        {"id": did, "t": tenant_id, "n": name},
    )
    xml = f"<opnsense><system><hostname>{name}</hostname></system></opnsense>"
    content_enc = crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8")))
    await s.execute(
        text(
            "INSERT INTO config_snapshots "
            "(id, tenant_id, device_id, canonical_hash, content_enc, opnsense_version, size_bytes) "
            "VALUES (:id, :t, :d, :h, :c, '24.7', :sz)"
        ),
        {
            "id": uuid.uuid4(),
            "t": tenant_id,
            "d": did,
            "h": canonical_hash,
            "c": content_enc,
            "sz": len(xml.encode("utf-8")),
        },
    )
    return did


async def test_config_snapshots_isolated_via_api(app_role_api_client, db_engine):
    """End-to-end config-snapshot isolation, across three levels of proof:

    (a) Behavior via the API (defense-in-depth: the repository applies an explicit
        application filter ``WHERE tenant_id = <tenant from the path>`` *and* RLS runs
        underneath). A sees only its own snapshots.
    (b) The real ``opngms_app`` reads its own rows through the API (grant propagation):
        positive assertion on A's snapshot.
    (c) It is RLS — not the application filter — that isolates cross-tenant: at the end
        a RAW query *without* ``WHERE tenant_id``, run as the real ``opngms_app`` role with
        context on tenant A, sees only A's rows. Without the application filter the only
        remaining defense is RLS, so this assertion would fail if RLS were disabled
        (it is not tautological).
    """
    ta, tb = await _setup(app_role_api_client, db_engine)

    # Seed devices + snapshots as owner (bypasses RLS) for both tenants.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev_a = await _seed_device_and_snapshot(s, ta, "fw-a", "HASH-A")
        await _seed_device_and_snapshot(s, tb, "fw-b", "HASH-B")
        await s.commit()

    # (a)+(b) real opngms_app reads its own snapshots through the API: POSITIVE assertion.
    ra = await app_role_api_client.get(f"/api/tenants/{ta}/devices/{dev_a}/config/snapshots")
    assert ra.status_code == 200
    assert [snap["canonical_hash"] for snap in ra.json()] == ["HASH-A"]
    # No cross-tenant leakage through the API (behavior / defense-in-depth).
    assert "HASH-B" not in [snap["canonical_hash"] for snap in ra.json()]

    # (c) Proof that it is RLS (not the application filter) that isolates cross-tenant.
    # DIRECT session as the real opngms_app role (NOT via the API, NOT as owner),
    # context on tenant A, RAW query WITHOUT WHERE tenant_id: the only remaining defense
    # is RLS. It must see ONLY A's rows -> it would fail if RLS were off.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        raw_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with raw_factory() as s:
            await set_tenant_context(s, ta)
            hashes = (
                await s.execute(text("SELECT canonical_hash FROM config_snapshots ORDER BY canonical_hash"))
            ).scalars().all()
            # NOT ["HASH-A", "HASH-B"]: without the application filter it is RLS that excludes B.
            assert hashes == ["HASH-A"]
        # And with no tenant context set at all -> RLS hides everything.
        async with raw_factory() as s2:
            none = (
                await s2.execute(text("SELECT canonical_hash FROM config_snapshots"))
            ).scalars().all()
            assert none == []
    finally:
        await engine.dispose()

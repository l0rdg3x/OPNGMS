import gzip
import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
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
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await app_role_api_client.post(
        "/api/login", json={"email": "sa@x.io", "password": "pw12345"}
    )
    return ta, tb


async def _seed_device_and_snapshot(s, tenant_id, name, hostname):
    """As owner (RLS-bypassing): insert a device + one encrypted snapshot. Returns device_id."""
    did = uuid.uuid4()
    key_enc = crypto.encrypt("apikey")
    secret_enc = crypto.encrypt("apisecret")
    await s.execute(
        text(
            "INSERT INTO devices "
            "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id, :t, :n, 'https://x', :k, :sec, true, 'reachable', '{}')"
        ),
        {"id": did, "t": tenant_id, "n": name, "k": key_enc, "sec": secret_enc},
    )
    xml = f"<opnsense><system><hostname>{hostname}</hostname></system></opnsense>"
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
            "h": f"hash-{hostname}",
            "c": content_enc,
            "sz": len(xml.encode("utf-8")),
        },
    )
    return did


async def test_config_model_isolated_via_api(app_role_api_client, db_engine):
    """End-to-end config-model isolation through the real ``opngms_app`` role.

    (a)+(b) tenant A reads its own device's model through the API (positive proof + grant
        propagation); A's hostname is present.
    (c) requesting tenant B's device id under A's tenant path returns 404: the snapshot is
        hidden by RLS (the repository sees no row for that device). It is RLS — not the
        application filter — that excludes B's snapshot, proven below by a RAW query.
    """
    ta, tb = await _setup(app_role_api_client, db_engine)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        dev_a = await _seed_device_and_snapshot(s, ta, "fw-a", "fwa")
        dev_b = await _seed_device_and_snapshot(s, tb, "fw-b", "fwb")
        await s.commit()

    # (a)+(b) real opngms_app reads A's own model: POSITIVE assertion.
    ra = await app_role_api_client.get(f"/api/tenants/{ta}/devices/{dev_a}/config/model")
    assert ra.status_code == 200
    hostname = ra.json()["children"][0]["children"][0]
    assert hostname["tag"] == "hostname"
    assert hostname["value"] == "fwa"

    # (c) B's device id under A's tenant path -> 404 (RLS hides the snapshot).
    rb = await app_role_api_client.get(f"/api/tenants/{ta}/devices/{dev_b}/config/model")
    assert rb.status_code == 404
    assert "fwb" not in rb.text  # B's content never leaks

    # Proof that it is RLS (not the application filter) that isolates cross-tenant.
    # DIRECT session as the real opngms_app role, context on tenant A, RAW query WITHOUT
    # WHERE tenant_id: the only remaining defense is RLS -> it must see ONLY A's rows.
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
                await s.execute(
                    text("SELECT canonical_hash FROM config_snapshots ORDER BY canonical_hash")
                )
            ).scalars().all()
            # NOT both rows: without the application filter it is RLS that excludes B.
            assert hashes == ["hash-fwa"]
        # And with no tenant context set at all -> RLS hides everything.
        async with raw_factory() as s2:
            none = (
                await s2.execute(text("SELECT canonical_hash FROM config_snapshots"))
            ).scalars().all()
            assert none == []
    finally:
        await engine.dispose()

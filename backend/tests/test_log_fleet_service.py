import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.services.log_fleet import fleet_forwarding_counts


async def _seed_tenant(s, *, slug, enabled, revoked, disabled):
    tid = uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                    {"i": tid, "n": slug.upper(), "sl": slug})
    await set_tenant_context(s, tid)
    n = 0
    for _ in range(enabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
    for _ in range(revoked):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,revoked_at) "
            "VALUES (:d,:t,false,'s','f',now())"), {"d": did, "t": tid})
    for _ in range(disabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,false,'s','f')"), {"d": did, "t": tid})
    return tid


async def test_fleet_forwarding_counts_per_tenant(db_engine):
    # Seed as the owner (db_engine = opngms superuser), where the per-INSERT RLS
    # context drives WITH CHECK on the tenant tables.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_tenant(s, slug="acme", enabled=2, revoked=1, disabled=0)
        tb = await _seed_tenant(s, slug="beta", enabled=1, revoked=0, disabled=1)
        await s.commit()
    # Count as the non-superuser app role (opngms_app), where RLS is actually
    # enforced. The owner role is BYPASSRLS, so the per-tenant set_tenant_context
    # loop would otherwise see every tenant's rows. This mirrors production, where
    # the API session connects as opngms_app.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username="opngms_app", password="opngms_app"
    )
    app_engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with app_factory() as s:
            counts = await fleet_forwarding_counts(s)
    finally:
        await app_engine.dispose()
    assert counts[ta]["enabled"] == 2 and counts[ta]["revoked"] == 1 and counts[ta]["disabled"] == 0
    assert counts[ta]["total_devices"] == 3 and counts[ta]["tenant_name"] == "ACME"
    assert counts[tb]["enabled"] == 1 and counts[tb]["disabled"] == 1 and counts[tb]["revoked"] == 0
    assert counts[tb]["total_devices"] == 2

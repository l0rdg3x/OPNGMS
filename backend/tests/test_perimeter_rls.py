import os
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD


async def _seed(db_engine, tenant_id, src_ip):
    """As owner: a device + one perimeter_attacker row for the tenant."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    now = datetime(2026, 6, 14, 12, 0, tzinfo=UTC)
    async with factory() as s:
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tenant_id})
        await s.execute(text(
            "INSERT INTO perimeter_attacker (device_id,kind,src_ip,tenant_id,count,first_seen,last_seen,detail) "
            "VALUES (:d,'firewall_block',:ip,:t,3,:n,:n,'{}'::jsonb)"),
            {"d": did, "ip": src_ip, "t": tenant_id, "n": now})
        await s.commit()
    return did


async def test_perimeter_attacker_isolated_by_rls(two_tenants, db_engine):
    """Proof that RLS — not an application filter — isolates the rollup cross-tenant.

    A direct session as the real opngms_app role, context on tenant A, runs a RAW query WITHOUT
    `WHERE tenant_id`: the only remaining defense is RLS. It must see ONLY A's row -> fails if RLS off.
    """
    ta, tb = two_tenants
    await _seed(db_engine, ta, "203.0.113.1")
    await _seed(db_engine, tb, "203.0.113.2")

    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, ta)
            ips = (await s.execute(text("SELECT src_ip FROM perimeter_attacker ORDER BY src_ip"))).scalars().all()
            assert ips == ["203.0.113.1"]  # B's row excluded by RLS alone
            # fail-closed: with no tenant context set, NULLIF predicate -> no rows.
            await s.execute(text("RESET app.current_tenant"))
            none = (await s.execute(text("SELECT count(*) FROM perimeter_attacker"))).scalar_one()
            assert none == 0
    finally:
        await engine.dispose()

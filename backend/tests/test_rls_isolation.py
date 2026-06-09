import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.core.rls import (
    TENANT_TABLES,
    disable_rls_statements,
    enable_rls_statements,
)
from app.repositories.device import DeviceRepository


def test_rls_statements_cover_devices():
    assert "devices" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.current_tenant'" in sql
    assert "WITH CHECK" in sql
    assert "tenant_id" in sql
    assert "NULLIF" in sql


def test_rls_statements_cover_metrics_and_alerts():
    assert "metrics" in TENANT_TABLES
    assert "alerts" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    for table in ("metrics", "alerts"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql


def test_disable_rls_statements_tear_down_policy():
    sql = "\n".join(disable_rls_statements())
    assert "DROP POLICY IF EXISTS" in sql
    assert "DISABLE ROW LEVEL SECURITY" in sql


async def test_repository_returns_only_active_tenant(db_engine, two_tenants):
    """App-level filter (DeviceRepository.list) returns only the active tenant's devices.

    Uses SET ROLE to the non-superuser app role so that Postgres RLS is active.
    """
    tenant_a, tenant_b = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        devices = await DeviceRepository(s, tenant_a).list()
        assert [d.name for d in devices] == ["fw-a"]


async def test_rls_blocks_cross_tenant_even_without_app_filter(db_engine, two_tenants):
    """Raw SELECT (no WHERE) — bypasses app filter. Postgres RLS must still isolate.

    Uses SET ROLE to the non-superuser app role so RLS is enforced by Postgres.
    """
    tenant_a, tenant_b = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == ["fw-a"]

        await set_tenant_context(s, tenant_b)
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == ["fw-b"]


async def test_no_tenant_context_sees_nothing(db_engine, two_tenants):
    """Without any tenant context set, the RLS policy returns zero rows.

    Uses SET ROLE to the non-superuser app role so Postgres RLS is active.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == []


async def test_app_role_connection_enforces_rls(db_engine, two_tenants):
    """Connessione REALE come ruolo non-superuser opngms_app: la RLS deve valere
    senza SET ROLE, esattamente come in produzione."""
    import os

    tenant_a, _ = two_tenants
    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    assert app_url.username == APP_ROLE  # fail loudly if the role didn't take
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
            assert rows == ["fw-a"]
        async with factory() as s2:
            # nessun contesto -> nessuna riga (fail-closed) anche per il ruolo reale
            rows = (await s2.execute(text("SELECT name FROM devices"))).scalars().all()
            assert rows == []
    finally:
        await engine.dispose()


async def test_metrics_alerts_isolated_cross_tenant(db_engine, two_tenants):
    """metrics e alerts: la connessione reale opngms_app vede solo il tenant in contesto.

    Prova anche la propagazione della RLS ai chunk dell'hypertable Timescale.
    """
    import os
    import uuid as _uuid
    from datetime import datetime, timezone

    tenant_a, tenant_b = two_tenants
    # device_id qualunque: la RLS filtra su tenant_id, non serve un device reale per la metrica.
    owner_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner_factory() as s:  # owner = superuser -> bypassa RLS, inserisce per entrambi
        for tid, val in ((tenant_a, 1.0), (tenant_b, 2.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": _uuid.uuid4(), "tid": tid, "v": val},
            )
        # alert: device_id deve riferire un device esistente (FK). two_tenants ha fw-a/fw-b.
        for tid, name in ((tenant_a, "fw-a"), (tenant_b, "fw-b")):
            dev_id = (
                await s.execute(text("SELECT id FROM devices WHERE name = :n"), {"n": name})
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO alerts "
                    "(id, tenant_id, device_id, type, label, severity, details) "
                    "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
                ),
                {"id": _uuid.uuid4(), "tid": tid, "did": dev_id},
            )
        await s.commit()

    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            vals = (await s.execute(text("SELECT value FROM metrics"))).scalars().all()
            assert vals == [1.0]
            sev = (await s.execute(text("SELECT severity FROM alerts"))).scalars().all()
            assert sev == ["critical"]
        async with factory() as s2:
            # nessun contesto -> fail-closed su entrambe
            assert (await s2.execute(text("SELECT value FROM metrics"))).scalars().all() == []
            assert (await s2.execute(text("SELECT id FROM alerts"))).scalars().all() == []
    finally:
        await engine.dispose()

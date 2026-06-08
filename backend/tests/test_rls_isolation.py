import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
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


def test_disable_rls_statements_tear_down_policy():
    sql = "\n".join(disable_rls_statements())
    assert "DROP POLICY IF EXISTS" in sql
    assert "DISABLE ROW LEVEL SECURITY" in sql


APP_ROLE = "opngms_app"


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

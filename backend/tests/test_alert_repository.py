import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.alert import AlertRepository


async def _seed_alerts(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner -> bypassa RLS
        await s.execute(
            text(
                "INSERT INTO alerts "
                "(id, tenant_id, device_id, type, label, severity, details) "
                "VALUES (:id, :tid, :did, 'device.down', '', 'critical', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "did": device_id},
        )
        await s.execute(
            text(
                "INSERT INTO alerts "
                "(id, tenant_id, device_id, type, label, severity, resolved_at, details) "
                "VALUES (:id, :tid, :did, 'gateway.down', 'WAN', 'warning', :r, '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "did": device_id, "r": datetime.now(timezone.utc)},
        )
        await s.commit()


async def test_list_active_only(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = (
        await _device_id_of(db_engine, "fw-a")
    )
    await _seed_alerts(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        alerts = await AlertRepository(s, tenant_a).list(active_only=True)
    assert [a.type for a in alerts] == ["device.down"]


async def test_list_all(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = await _device_id_of(db_engine, "fw-a")
    await _seed_alerts(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        alerts = await AlertRepository(s, tenant_a).list(active_only=False)
    assert {a.type for a in alerts} == {"device.down", "gateway.down"}


async def _device_id_of(db_engine, name):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        return (
            await s.execute(text("SELECT id FROM devices WHERE name = :n"), {"n": name})
        ).scalar_one()

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.metric import MetricRepository


async def _seed(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypassa RLS
        for i, v in enumerate((10.0, 20.0, 30.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "tid": tenant_id, "v": v},
            )
        await s.commit()
    return base


async def test_series_returns_points_in_order(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load", base - timedelta(minutes=1), base + timedelta(minutes=10), None
        )
    assert [p.value for p in points] == [10.0, 20.0, 30.0]
    assert all(p.label == "" for p in points)


async def test_last_returns_latest_per_label(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        last = await repo.last(device_id, "cpu.load")
    assert [p.value for p in last] == [30.0]


async def test_series_bucket_downsamples(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load",
            base - timedelta(minutes=1), base + timedelta(minutes=10),
            timedelta(hours=1),  # un bucket -> media (10+20+30)/3 = 20
        )
    assert len(points) == 1
    assert points[0].value == 20.0

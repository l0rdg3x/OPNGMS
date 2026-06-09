import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.event import EventRepository


async def _seed(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypasses RLS
        for i, (src, name) in enumerate([("ids", "ET SCAN"), ("ids", "ET POLICY"), ("dns", "example.com")]):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, :src, :k, :tid, :name, '10.0.0.5')"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "src": src,
                 "k": f"k{i}", "tid": tenant_id, "name": name},
            )
        await s.commit()
    return base


async def test_list_returns_most_recent_first(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source=None, device_id=None, frm=None, to=None, limit=100
        )
    assert [r.name for r in rows] == ["example.com", "ET POLICY", "ET SCAN"]  # DESC by time


async def test_list_filters_by_source(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source="dns", device_id=None, frm=None, to=None, limit=100
        )
    assert [r.source for r in rows] == ["dns"]
    assert rows[0].name == "example.com"


async def test_list_respects_limit(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source=None, device_id=None, frm=None, to=None, limit=2
        )
    assert len(rows) == 2  # the 2 most recent

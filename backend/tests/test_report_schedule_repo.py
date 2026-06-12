import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.report_schedule import ReportScheduleRepository


async def _tenant_device(s):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
    await set_tenant_context(s, tid)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
    return tid, did


async def test_upsert_tenant_then_device_and_list(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, did = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        now = datetime(2026, 6, 10, 9, tzinfo=UTC)
        t = await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                              recipients=["a@x.io"], created_by=None, now=now)
        assert t.next_run_at == datetime(2026, 6, 15, 4, tzinfo=UTC)
        d = await repo.upsert(device_id=did, enabled=True, frequency="monthly", weekday=None, hour=5,
                              recipients=["b@x.io"], created_by=None, now=now)
        assert d.next_run_at == datetime(2026, 7, 1, 5, tzinfo=UTC)
        rows = await repo.list()
        assert {r.device_id for r in rows} == {None, did}


async def test_upsert_is_idempotent_per_scope(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        now = datetime(2026, 6, 10, 9, tzinfo=UTC)
        await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                          recipients=["a@x.io"], created_by=None, now=now)
        await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=2, hour=6,
                          recipients=["c@x.io"], created_by=None, now=now)
        rows = await repo.list()
        assert len(rows) == 1
        assert rows[0].weekday == 2 and rows[0].recipients == ["c@x.io"]


async def test_on_demand_has_null_next_run(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        r = await repo.upsert(device_id=None, enabled=True, frequency="on_demand", weekday=None,
                              hour=4, recipients=["a@x.io"], created_by=None,
                              now=datetime(2026, 6, 10, 9, tzinfo=UTC))
        assert r.next_run_at is None


async def test_delete(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        r = await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                              recipients=["a@x.io"], created_by=None, now=datetime(2026, 6, 10, 9, tzinfo=UTC))
        assert await repo.delete(r.id) is True
        assert await repo.list() == []

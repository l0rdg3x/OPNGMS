import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.report_schedule import ReportSchedule


async def _tenant(s):
    tid = uuid.uuid4()
    await s.execute(
        text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'A', 'a', 'active')"),
        {"id": tid},
    )
    return tid


async def test_report_schedule_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid = await _tenant(s)
        s.add(ReportSchedule(
            tenant_id=tid, device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
            recipients=["a@x.io"], next_run_at=datetime(2026, 6, 15, 4, tzinfo=UTC),
        ))
        await s.commit()
        row = (await s.execute(select(ReportSchedule))).scalar_one()
        assert row.frequency == "weekly"
        assert row.recipients == ["a@x.io"]
        assert row.device_id is None

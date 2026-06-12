import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.worker as worker
from app.core.db import set_tenant_context
from app.models.generated_report import GeneratedReport
from app.models.report_schedule import ReportSchedule


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, *a, **k):
        self.calls.append((name, a, k))


async def test_device_scoped_delivery_stores_device_report(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw-x','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(ReportSchedule(tenant_id=tid, device_id=did, enabled=True, frequency="monthly",
                             weekday=None, hour=4, recipients=["a@x.io"],
                             next_run_at=datetime(2020, 1, 1, tzinfo=UTC)))
        await s.commit()
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()

    res = await worker.deliver_scheduled_report({"session_factory": factory, "redis": FakeRedis()}, str(sid))
    assert res == "generated"
    async with factory() as s:
        rep = (await s.execute(select(GeneratedReport))).scalar_one()
        assert rep.device_id == did

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_event_insert_and_dedup(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    did = uuid.uuid4()
    async with factory() as s:  # owner -> bypassa RLS
        for _ in range(2):  # due insert identici -> dedup via PK
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN', '10.0.0.5') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"t": now, "d": did, "tid": tenant_a},
            )
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert n == 1  # il secondo insert è stato deduplicato

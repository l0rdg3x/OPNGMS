import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


async def test_config_snapshot_insert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner -> bypasses RLS
        # two_tenants seeds device 'fw-a' for tenant A; use it (FK device_id -> devices.id).
        did = (
            await s.execute(text("SELECT id FROM devices WHERE name = 'fw-a'"))
        ).scalar_one()
        await s.execute(
            text(
                "INSERT INTO config_snapshots (id, tenant_id, device_id, canonical_hash, content_enc) "
                "VALUES (:id, :tid, :did, 'h1', '\\x00'::bytea)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_a, "did": did},
        )
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM config_snapshots"))).scalar_one()
    assert n == 1

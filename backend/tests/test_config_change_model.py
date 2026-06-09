import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.config_change import ConfigChange


async def test_config_change_insert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner -> bypasses RLS
        # two_tenants seeds device 'fw-a' for tenant A; use it (FK device_id -> devices.id).
        did = (
            await s.execute(text("SELECT id FROM devices WHERE name = 'fw-a'"))
        ).scalar_one()
        change = ConfigChange(
            tenant_id=tenant_a,
            device_id=did,
            created_by=uuid.uuid4(),
            kind="alias",
            operation="set",
            target="myalias",
            payload={"name": "myalias", "content": ["1.2.3.4"]},
            baseline_hash="base-h",
        )
        s.add(change)
        await s.commit()
        cid = change.id
    async with factory() as s:
        row = await s.get(ConfigChange, cid)
    # server_defaults applied for the unset columns.
    assert row.status == "draft"
    assert row.target == "myalias"
    assert row.baseline_hash == "base-h"
    assert row.payload == {"name": "myalias", "content": ["1.2.3.4"]}
    assert row.result == {}
    assert row.scheduled_at is None
    assert row.applied_at is None
    assert row.created_at is not None
    assert row.updated_at is not None

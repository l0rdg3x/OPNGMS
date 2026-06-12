import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from app.services.config_revert import RevertError, revert_change


async def _seed(factory):
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        change = ConfigChange(tenant_id=tid, device_id=did, created_by=uuid.uuid4(), kind="alias",
                              operation="add", target="A", payload={"name": "A", "type": "host"},
                              baseline_hash="", status="applied",
                              applied_at=datetime(2026, 6, 1, tzinfo=timezone.utc))
        s.add(change)
        await s.commit()
        return tid, did, change.id


async def test_revert_creates_linked_inverse(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did, cid = await _seed(factory)
    async with factory() as s:
        await set_tenant_context(s, tid)
        change = await s.get(ConfigChange, cid)
        inverse = await revert_change(s, change, actor_id=uuid.uuid4())
        await s.commit()
        assert inverse.operation == "delete"
        assert inverse.reverts_change_id == cid
        assert inverse.kind == "alias"
        assert inverse.status == "draft"


async def test_revert_rejects_non_revertible_state(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did, cid = await _seed(factory)
    async with factory() as s:
        await set_tenant_context(s, tid)
        change = await s.get(ConfigChange, cid)
        change.status = "scheduled"
        with pytest.raises(RevertError):
            await revert_change(s, change, actor_id=uuid.uuid4())

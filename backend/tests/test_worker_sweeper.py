import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.worker as worker
from app.core.db import set_tenant_context
from app.models.config_change import ConfigChange
from app.services.config_push import _advisory_key


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))


async def _seed_change(factory, *, status="scheduled", scheduled_at, sweep_attempts=0):
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        c = ConfigChange(tenant_id=tid, device_id=did, created_by=uuid.uuid4(), kind="alias",
                         operation="add", target="A", payload={}, baseline_hash="", status=status,
                         scheduled_at=scheduled_at, sweep_attempts=sweep_attempts)
        s.add(c)
        await s.commit()
        return tid, did, c.id


async def test_overdue_orphan_is_reenqueued(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, _, cid = await _seed_change(factory, scheduled_at=past)
    redis = FakeRedis()
    summary = await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls and redis.calls[0][0] == "apply_config_change"
    assert redis.calls[0][1][0] == str(cid)
    async with factory() as s:
        assert (await s.get(ConfigChange, cid)).sweep_attempts == 1
    assert summary["re_enqueued"] >= 1


async def test_recent_scheduled_within_grace_is_untouched(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    soon = datetime.now(UTC) - timedelta(seconds=30)
    await _seed_change(factory, scheduled_at=soon)
    redis = FakeRedis()
    await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls == []


async def test_device_busy_is_skipped(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    _, did, cid = await _seed_change(factory, scheduled_at=past)
    holder = async_sessionmaker(db_engine, expire_on_commit=False)
    async with holder() as hs:
        await hs.execute(text("SELECT pg_advisory_lock(:k)"), {"k": _advisory_key(did)})
        redis = FakeRedis()
        summary = await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
        await hs.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _advisory_key(did)})
    assert redis.calls == []
    assert summary["skipped"] >= 1
    async with factory() as s:
        assert (await s.get(ConfigChange, cid)).sweep_attempts == 0


async def test_attempts_exhausted_gives_up(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    past = datetime.now(UTC) - timedelta(hours=1)
    tid, did, cid = await _seed_change(factory, scheduled_at=past, sweep_attempts=5)
    redis = FakeRedis()
    await worker.sweep_orphaned_actions({"session_factory": factory, "redis": redis})
    assert redis.calls == []
    async with factory() as s:
        c = await s.get(ConfigChange, cid)
        assert c.status == "failed"
        assert "orphaned" in c.result.get("error", "")
        n = (await s.execute(text("SELECT count(*) FROM alerts WHERE device_id=:d"), {"d": did})).scalar_one()
        assert n >= 1

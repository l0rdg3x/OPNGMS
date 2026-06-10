import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.auth import AuthService
from app.worker import cleanup_expired_sessions


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_cleanup_cron_purges_expired(factory):
    uid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, status, is_superadmin) "
                "VALUES (:id, :e, 'T', 'x', 'active', true)"
            ),
            {"id": uid, "e": f"{uid}@t.io"},
        )
        await s.commit()
        user = await s.get(__import__("app.models.user", fromlist=["User"]).User, uid)
        svc = AuthService(s)
        live, _ = await svc.create_session(user, ttl_hours=12)
        dead, _ = await svc.create_session(user, ttl_hours=12)
        dead.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()

    result = await cleanup_expired_sessions({"session_factory": factory})
    assert "purged 1" in result
    async with factory() as s:
        remaining = (await s.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        assert remaining == 1


async def test_cleanup_cron_purges_idle_sessions(factory):
    uid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, status, is_superadmin) "
                "VALUES (:id, :e, 'T', 'x', 'active', true)"
            ),
            {"id": uid, "e": f"{uid}@t.io"},
        )
        await s.commit()
        user = await s.get(__import__("app.models.user", fromlist=["User"]).User, uid)
        svc = AuthService(s)
        # Live session: last_seen_at is recent, expires_at in the future.
        live, _ = await svc.create_session(user, ttl_hours=12)
        # Idle session: expires_at still in the future, but last_seen_at is beyond the
        # 120-minute idle window so it should be reaped by purge_expired().
        idle, _ = await svc.create_session(user, ttl_hours=12)
        idle.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=121)
        await s.commit()

    result = await cleanup_expired_sessions({"session_factory": factory})
    assert "purged 1" in result
    async with factory() as s:
        remaining = (await s.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        assert remaining == 1

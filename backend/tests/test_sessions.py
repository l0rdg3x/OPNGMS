import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.session import Session


@pytest.fixture
async def factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _make_user(factory) -> uuid.UUID:
    uid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO users (id, email, name, password_hash, status, is_superadmin) "
                "VALUES (:id, :email, 'T', 'x', 'active', true)"
            ),
            {"id": uid, "email": f"{uid}@t.io"},
        )
        await s.commit()
    return uid


async def test_session_row_has_hardening_columns(factory):
    uid = await _make_user(factory)
    now = datetime.now(timezone.utc)
    async with factory() as s:
        sess = Session(
            user_id=uid,
            token_hash="a" * 64,
            csrf_token="c" * 43,
            last_seen_at=now,
            expires_at=now + timedelta(hours=12),
            ip="203.0.113.5",
            user_agent="pytest",
        )
        s.add(sess)
        await s.commit()
        row = (await s.execute(text("SELECT token_hash, csrf_token, ip, user_agent FROM sessions WHERE id=:i"), {"i": sess.id})).one()
        assert row.token_hash == "a" * 64
        assert row.ip == "203.0.113.5"


def test_settings_have_idle_timeout():
    from app.core.config import Settings

    s = Settings(database_url="x", session_secret="x", master_key="x")
    assert s.session_idle_minutes == 120


def test_csrf_cookie_constant_exists():
    from app.core.deps import CSRF_COOKIE

    assert CSRF_COOKIE == "opngms_csrf"

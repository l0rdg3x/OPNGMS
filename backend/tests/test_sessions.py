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


from app.models.user import User
from app.services.auth import AuthService, _hash_token


async def _user_obj(factory) -> User:
    uid = await _make_user(factory)
    async with factory() as s:
        return await s.get(User, uid)


async def test_create_session_hashes_token(factory):
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        sess, raw = await AuthService(s).create_session(user, ttl_hours=12, ip="203.0.113.9", user_agent="UA")
        await s.commit()
        assert raw and sess.token_hash == _hash_token(raw)
        assert sess.token_hash != raw  # stored value is the hash, not the token
        assert sess.csrf_token and sess.ip == "203.0.113.9"


async def test_get_session_for_token_roundtrip_and_expiry(factory):
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        sess, raw = await svc.create_session(user, ttl_hours=12)
        await s.commit()
        got = await svc.get_session_for_token(raw)
        assert got is not None and got.id == sess.id
        assert await svc.get_session_for_token("not-a-real-token") is None


async def test_idle_timeout_rejects_stale_session(factory):
    from datetime import datetime, timedelta, timezone
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        sess, raw = await svc.create_session(user, ttl_hours=12)
        sess.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=121)  # idle default 120
        await s.commit()
        assert await svc.get_session_for_token(raw) is None


async def test_logout_all_and_purge(factory):
    from datetime import datetime, timedelta, timezone
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        a, ra = await svc.create_session(user, ttl_hours=12)
        b, rb = await svc.create_session(user, ttl_hours=12)
        await s.commit()
        assert len(await svc.list_sessions_for_user(user.id)) == 2
        await svc.delete_all_sessions_for_user(user.id)
        await s.commit()
        assert await svc.list_sessions_for_user(user.id) == []
        # purge: insert one already-expired session, confirm it is removed
        c, rc = await svc.create_session(user, ttl_hours=12)
        c.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()
        n = await svc.purge_expired(datetime.now(timezone.utc))
        await s.commit()
        assert n == 1 and await svc.list_sessions_for_user(user.id) == []


async def _setup_login(api_client):
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456"})
    await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456"})


async def test_login_sets_both_cookies(api_client):
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456"})
    r = await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456"})
    assert r.status_code == 200
    assert api_client.cookies.get("opngms_session")
    assert api_client.cookies.get("opngms_csrf")


async def test_get_sessions_lists_current(api_client):
    await _setup_login(api_client)
    r = await api_client.get("/api/sessions")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and rows[0]["current"] is True
    assert "ip" in rows[0] and "user_agent" in rows[0]


async def test_logout_all_kills_every_session(api_client):
    await _setup_login(api_client)
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post("/api/logout-all", headers={"X-OPNGMS-CSRF": csrf})
    assert r.status_code == 204
    assert (await api_client.get("/api/me")).status_code == 401

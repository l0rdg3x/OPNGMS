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
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456-secure"})
    await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456-secure"})


async def test_login_sets_both_cookies(api_client):
    await api_client.post("/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456-secure"})
    r = await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456-secure"})
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


async def test_tampered_session_cookie_returns_401(api_client):
    await _setup_login(api_client)
    # The server sets cookies under domain "test.local" (httpx normalises the
    # base_url host "test" to "test.local" in the cookie jar).  We must match
    # that domain when overwriting so the tampered value replaces the real one.
    api_client.cookies.set("opngms_session", "tampered-not-a-real-token", domain="test.local")
    r = await api_client.get("/api/me")
    assert r.status_code == 401


async def test_login_rotation_deletes_old_session(api_client, db_engine):
    from sqlalchemy import text as _text
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm

    # Setup: create user then log in twice; second login must delete the first session.
    await api_client.post("/api/setup", json={"email": "rot@rot.io", "name": "R", "password": "pw-123456-secure"})
    await api_client.post("/api/login", json={"email": "rot@rot.io", "password": "pw-123456-secure"})
    # Second login carries the first session cookie, triggering anti-fixation rotation.
    await api_client.post("/api/login", json={"email": "rot@rot.io", "password": "pw-123456-secure"})

    factory = _asm(db_engine, expire_on_commit=False)
    async with factory() as s:
        # Look up user id by email.
        row = (
            await s.execute(_text("SELECT id FROM users WHERE email='rot@rot.io'"))
        ).one()
        uid = row.id
        count = (
            await s.execute(
                _text("SELECT count(*) FROM sessions WHERE user_id=:uid"), {"uid": uid}
            )
        ).scalar_one()
    assert count == 1


async def test_absolute_expiry_enforced_at_service_level(factory):
    async with factory() as s:
        user = await s.get(User, await _make_user(factory))
        svc = AuthService(s)
        sess, raw = await svc.create_session(user, ttl_hours=12)
        # Force the session to have expired an hour ago.
        sess.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await s.commit()
        result = await svc.get_session_for_token(raw)
    assert result is None


async def test_app_ignores_raw_x_forwarded_for(api_client, db_engine):
    # The app must derive the client IP from request.client.host (populated by uvicorn's vetted
    # proxy-headers middleware behind a trusted proxy + nginx sanitising X-Forwarded-For), NOT from a
    # raw inbound X-Forwarded-For. Otherwise a client could spoof its IP to evade the per-IP login
    # lockout. Logging in with a forged header must NOT record the forged IP on the session.
    from sqlalchemy.ext.asyncio import async_sessionmaker as _asm

    await api_client.post("/api/setup", json={"email": "xff@xff.io", "name": "X", "password": "pw-123456-secure"})
    await api_client.post(
        "/api/login",
        json={"email": "xff@xff.io", "password": "pw-123456-secure"},
        headers={"X-Forwarded-For": "9.9.9.9"},
    )
    factory = _asm(db_engine, expire_on_commit=False)
    async with factory() as s:
        ip = (await s.execute(text("SELECT ip FROM sessions LIMIT 1"))).scalar_one()
    assert ip != "9.9.9.9"

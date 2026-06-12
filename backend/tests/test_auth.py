import pytest


def test_session_token_hash_is_keyed_by_session_secret(monkeypatch):
    # The stored token_hash is HMAC(SESSION_SECRET, token): rotating SESSION_SECRET must change the
    # hash of the same raw token (so a rotation invalidates every existing session).
    from types import SimpleNamespace

    from app.services import auth as auth_mod

    monkeypatch.setattr(auth_mod, "get_settings", lambda: SimpleNamespace(session_secret="secret-A"))
    h_a = auth_mod._hash_token("the-token")
    monkeypatch.setattr(auth_mod, "get_settings", lambda: SimpleNamespace(session_secret="secret-B"))
    h_b = auth_mod._hash_token("the-token")
    assert h_a != h_b and len(h_a) == 64


async def _setup_admin(api_client):
    await api_client.post(
        "/api/setup",
        json={"email": "admin@x.io", "name": "Admin", "password": "pw12345-secure"},
    )


@pytest.mark.asyncio
async def test_login_sets_cookie_and_me_returns_user(api_client):
    await _setup_admin(api_client)
    resp = await api_client.post(
        "/api/login", json={"email": "admin@x.io", "password": "pw12345-secure"}
    )
    assert resp.status_code == 200
    assert "opngms_session" in resp.cookies
    me = await api_client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["email"] == "admin@x.io"


@pytest.mark.asyncio
async def test_login_wrong_password_401(api_client):
    await _setup_admin(api_client)
    resp = await api_client.post(
        "/api/login", json={"email": "admin@x.io", "password": "nope"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_without_session_401(api_client):
    resp = await api_client.get("/api/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_session(api_client):
    await _setup_admin(api_client)
    await api_client.post("/api/login", json={"email": "admin@x.io", "password": "pw12345-secure"})
    csrf = api_client.cookies.get("opngms_csrf")
    out = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": csrf})
    assert out.status_code == 204
    me = await api_client.get("/api/me")
    assert me.status_code == 401

import pytest


async def _setup_admin(api_client):
    await api_client.post(
        "/api/setup",
        json={"email": "admin@x.io", "name": "Admin", "password": "pw12345"},
    )


@pytest.mark.asyncio
async def test_login_sets_cookie_and_me_returns_user(api_client):
    await _setup_admin(api_client)
    resp = await api_client.post(
        "/api/login", json={"email": "admin@x.io", "password": "pw12345"}
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
    await api_client.post("/api/login", json={"email": "admin@x.io", "password": "pw12345"})
    out = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "1"})
    assert out.status_code == 204
    me = await api_client.get("/api/me")
    assert me.status_code == 401

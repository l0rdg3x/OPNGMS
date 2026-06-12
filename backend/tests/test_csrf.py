async def _login(api_client):
    await api_client.post(
        "/api/setup", json={"email": "a@a.io", "name": "A", "password": "pw-123456-secure"}
    )
    await api_client.post("/api/login", json={"email": "a@a.io", "password": "pw-123456-secure"})


def _csrf(api_client) -> str:
    # The login response set a readable opngms_csrf cookie; httpx stores it in the jar.
    return api_client.cookies.get("opngms_csrf")


async def test_logout_without_header_is_forbidden(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout")
    assert r.status_code == 403


async def test_logout_with_wrong_token_is_forbidden(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "wrong"})
    assert r.status_code == 403


async def test_logout_with_session_token_succeeds(api_client):
    await _login(api_client)
    r = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": _csrf(api_client)})
    assert r.status_code == 204

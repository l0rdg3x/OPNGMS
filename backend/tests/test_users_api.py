async def _login_superadmin(api_client):
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})


CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_superadmin_creates_user(api_client):
    await _login_superadmin(api_client)
    resp = await api_client.post(
        "/api/users",
        json={"email": "u@x.io", "name": "U", "password": "pw12345", "is_superadmin": False},
        headers=CSRF,
    )
    assert resp.status_code == 201
    assert resp.json()["email"] == "u@x.io"
    listed = await api_client.get("/api/users")
    assert any(u["email"] == "u@x.io" for u in listed.json())


async def test_create_user_duplicate_email_409(api_client):
    await _login_superadmin(api_client)
    body = {"email": "dup@x.io", "name": "D", "password": "pw12345", "is_superadmin": False}
    assert (await api_client.post("/api/users", json=body, headers=CSRF)).status_code == 201
    assert (await api_client.post("/api/users", json=body, headers=CSRF)).status_code == 409

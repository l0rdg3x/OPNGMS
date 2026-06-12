import pytest


@pytest.mark.asyncio
async def test_setup_creates_first_superadmin(api_client):
    resp = await api_client.post(
        "/api/setup",
        json={"email": "admin@x.io", "name": "Admin", "password": "pw12345-secure"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "admin@x.io"
    assert body["is_superadmin"] is True


@pytest.mark.asyncio
async def test_setup_rejects_short_password(api_client):
    # The first superadmin must meet the minimum password length (no weak break-glass account).
    resp = await api_client.post(
        "/api/setup", json={"email": "admin@x.io", "name": "Admin", "password": "short"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_setup_disabled_once_a_user_exists(api_client):
    first = await api_client.post(
        "/api/setup",
        json={"email": "a@x.io", "name": "A", "password": "pw12345-secure"},
    )
    assert first.status_code == 201
    second = await api_client.post(
        "/api/setup",
        json={"email": "b@x.io", "name": "B", "password": "pw12345-secure"},
    )
    assert second.status_code == 409

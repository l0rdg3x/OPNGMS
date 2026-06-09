import pytest


async def _login(api_client):
    await api_client.post(
        "/api/setup", json={"email": "a@x.io", "name": "A", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "a@x.io", "password": "pw12345"})


@pytest.mark.asyncio
async def test_mutation_without_csrf_header_rejected(api_client):
    await _login(api_client)
    resp = await api_client.post("/api/logout")  # no CSRF header
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_mutation_with_csrf_header_allowed(api_client):
    await _login(api_client)
    resp = await api_client.post("/api/logout", headers={"X-OPNGMS-CSRF": "1"})
    assert resp.status_code == 204

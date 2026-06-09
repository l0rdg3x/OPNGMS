import pytest

pytestmark = pytest.mark.asyncio

CSRF = {"X-OPNGMS-CSRF": "1"}


async def test_full_admin_flow(api_client):
    # 1. setup first superadmin
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    # 2. login superadmin
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    # 3. create tenant
    t = await api_client.post(
        "/api/tenants", json={"name": "Acme", "slug": "acme"}, headers=CSRF
    )
    tenant_id = t.json()["id"]
    # 4. create operator user
    u = await api_client.post(
        "/api/users",
        json={"email": "op@x.io", "name": "Op", "password": "pw12345", "is_superadmin": False},
        headers=CSRF,
    )
    user_id = u.json()["id"]
    # 5. assign operator membership
    m = await api_client.post(
        f"/api/tenants/{tenant_id}/memberships",
        json={"user_id": user_id, "role": "operator"},
        headers=CSRF,
    )
    assert m.status_code == 201
    # 6. logout superadmin, login operator
    await api_client.post("/api/logout", headers=CSRF)
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    # 7. the operator CANNOT create tenants (org-level)
    denied = await api_client.post(
        "/api/tenants", json={"name": "X", "slug": "x"}, headers=CSRF
    )
    assert denied.status_code == 403
    # 8. the operator is a member but does NOT have membership.manage -> 403 on list memberships
    ms = await api_client.get(f"/api/tenants/{tenant_id}/memberships")
    assert ms.status_code == 403

import pyotp
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.services import mfa as mfa_svc
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _seed(db_engine):
    """Create a superadmin, a normal user, and a target user that has MFA enabled."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345", is_superadmin=True)
        await make_user(s, email="reg@x.io", password="pw12345")
        target = await make_user(s, email="tgt@x.io", password="pw12345")
        s.add(
            UserMfa(
                user_id=target.id,
                enabled=True,
                totp_secret_enc=crypto.encrypt(pyotp.random_base32()),
            )
        )
        _, hashes = mfa_svc.generate_recovery_codes(2)
        for h in hashes:
            s.add(UserRecoveryCode(user_id=target.id, code_hash=h))
        await s.commit()
        return target.id


async def _login(api_client, email, password="pw12345"):
    r = await api_client.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


async def test_superadmin_get_and_set_policy(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    g = await api_client.get("/api/admin/mfa-policy")
    assert g.status_code == 200
    assert g.json()["mode"] == "off"
    p = await api_client.put(
        "/api/admin/mfa-policy", json={"mode": "privileged"}, headers=csrf_headers(api_client)
    )
    assert p.status_code == 200, p.text
    assert p.json()["mode"] == "privileged"
    g2 = await api_client.get("/api/admin/mfa-policy")
    assert g2.json()["mode"] == "privileged"


async def test_non_superadmin_policy_is_403(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "reg@x.io")
    g = await api_client.get("/api/admin/mfa-policy")
    assert g.status_code == 403
    p = await api_client.put(
        "/api/admin/mfa-policy", json={"mode": "all"}, headers=csrf_headers(api_client)
    )
    assert p.status_code == 403


async def test_set_policy_rejects_invalid_mode(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    p = await api_client.put(
        "/api/admin/mfa-policy", json={"mode": "nonsense"}, headers=csrf_headers(api_client)
    )
    assert p.status_code == 422


async def test_superadmin_resets_target_mfa(api_client, db_engine):
    target_id = await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post(
        f"/api/users/{target_id}/mfa/reset", headers=csrf_headers(api_client)
    )
    assert r.status_code == 204, r.text
    # verify the row + recovery codes are gone
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await s.get(UserMfa, target_id) is None


async def test_non_superadmin_reset_is_403(api_client, db_engine):
    target_id = await _seed(db_engine)
    await _login(api_client, "reg@x.io")
    r = await api_client.post(
        f"/api/users/{target_id}/mfa/reset", headers=csrf_headers(api_client)
    )
    assert r.status_code == 403

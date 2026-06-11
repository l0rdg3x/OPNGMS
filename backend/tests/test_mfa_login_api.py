import pyotp
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.models.user_mfa import UserMfa
from app.models.user_recovery_code import UserRecoveryCode
from app.services import mfa as mfa_svc
from tests.conftest import csrf_headers
from tests.factories import make_user

_SECRET = pyotp.random_base32()


async def _seed_mfa_user(db_engine, email="m@x.io", password="pw12345", recovery=None):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        user = await make_user(s, email=email, password=password)
        s.add(
            UserMfa(
                user_id=user.id,
                enabled=True,
                totp_secret_enc=crypto.encrypt(_SECRET),
            )
        )
        if recovery:
            for h in recovery:
                s.add(UserRecoveryCode(user_id=user.id, code_hash=h))
        await s.commit()
        return user.id


async def test_login_with_mfa_returns_mfa_required_and_blocks_app(api_client, db_engine):
    await _seed_mfa_user(db_engine)
    r = await api_client.post("/api/login", json={"email": "m@x.io", "password": "pw12345"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "mfa_required"
    assert r.json()["user"] is None
    # the pending session cannot reach a normal (get_current_user) app endpoint
    sessions = await api_client.get("/api/sessions")
    assert sessions.status_code == 403
    assert sessions.json()["detail"] == "mfa_required"
    # /api/me (enrollment-aware) treats a pending session as unauthenticated
    me = await api_client.get("/api/me")
    assert me.status_code == 401


async def test_login_mfa_completes_with_totp(api_client, db_engine):
    await _seed_mfa_user(db_engine)
    await api_client.post("/api/login", json={"email": "m@x.io", "password": "pw12345"})
    h = csrf_headers(api_client)
    code = pyotp.TOTP(_SECRET).now()
    r = await api_client.post("/api/login/mfa", json={"code": code}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["user"]["email"] == "m@x.io"
    # now a full session: app endpoint works
    me = await api_client.get("/api/me")
    assert me.status_code == 200


async def test_login_mfa_wrong_code_is_401(api_client, db_engine):
    await _seed_mfa_user(db_engine)
    await api_client.post("/api/login", json={"email": "m@x.io", "password": "pw12345"})
    h = csrf_headers(api_client)
    r = await api_client.post("/api/login/mfa", json={"code": "000000"}, headers=h)
    assert r.status_code == 401


async def test_login_mfa_recovery_code_single_use(api_client, db_engine):
    codes, hashes = mfa_svc.generate_recovery_codes(3)
    await _seed_mfa_user(db_engine, recovery=hashes)
    await api_client.post("/api/login", json={"email": "m@x.io", "password": "pw12345"})
    h = csrf_headers(api_client)
    r = await api_client.post("/api/login/mfa", json={"code": codes[0]}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"

    # the same recovery code cannot be reused: log in again and replay it
    await api_client.post("/api/logout", headers=csrf_headers(api_client))
    await api_client.post("/api/login", json={"email": "m@x.io", "password": "pw12345"})
    h2 = csrf_headers(api_client)
    r2 = await api_client.post("/api/login/mfa", json={"code": codes[0]}, headers=h2)
    assert r2.status_code == 401


async def test_setup_policy_issues_setup_session(api_client, db_engine):
    # user with no MFA but a global "all" policy -> setup-only session
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="p@x.io", password="pw12345")
        await s.commit()
    from app.services.app_settings import set_mfa_policy
    async with factory() as s:
        await set_mfa_policy(s, "all")
        await s.commit()
    r = await api_client.post("/api/login", json={"email": "p@x.io", "password": "pw12345"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "mfa_setup_required"
    assert r.json()["user"]["mfa_setup_required"] is True
    # the setup-only session may reach the enrollment endpoints but not the app
    me = await api_client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["mfa_setup_required"] is True
    st = await api_client.get("/api/me/mfa")
    assert st.status_code == 200

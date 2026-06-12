import pyotp
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_user


async def _seed_user(db_engine, email="u@x.io", password="pw12345-secure"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email=email, password=password)
        await s.commit()


async def _login(api_client, email="u@x.io", password="pw12345-secure"):
    r = await api_client.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


async def test_enroll_confirm_and_status(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)

    r = await api_client.post("/api/me/mfa/setup", json={"password": "pw12345-secure"}, headers=h)
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    assert secret and r.json()["otpauth_uri"].startswith("otpauth://totp/")

    code = pyotp.TOTP(secret).now()
    r2 = await api_client.post("/api/me/mfa/confirm", json={"code": code}, headers=h)
    assert r2.status_code == 200, r2.text
    assert len(r2.json()["recovery_codes"]) == 10

    r3 = await api_client.get("/api/me/mfa")
    assert r3.status_code == 200, r3.text
    assert r3.json()["enabled"] is True
    assert r3.json()["recovery_codes_remaining"] == 10


async def test_setup_rejects_wrong_password(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/setup", json={"password": "WRONG"}, headers=h)
    assert r.status_code == 403


async def test_confirm_rejects_wrong_code(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/setup", json={"password": "pw12345-secure"}, headers=h)
    assert r.status_code == 200
    r2 = await api_client.post("/api/me/mfa/confirm", json={"code": "000000"}, headers=h)
    assert r2.status_code == 422


async def test_disable_requires_password_and_clears_mfa(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/setup", json={"password": "pw12345-secure"}, headers=h)
    secret = r.json()["secret"]
    code = pyotp.TOTP(secret).now()
    await api_client.post("/api/me/mfa/confirm", json={"code": code}, headers=h)

    # wrong password rejected
    rbad = await api_client.post("/api/me/mfa/disable", json={"password": "WRONG"}, headers=h)
    assert rbad.status_code == 403
    # correct password disables
    rok = await api_client.post("/api/me/mfa/disable", json={"password": "pw12345-secure"}, headers=h)
    assert rok.status_code == 204
    status = await api_client.get("/api/me/mfa")
    assert status.json()["enabled"] is False
    assert status.json()["recovery_codes_remaining"] == 0


async def test_regenerate_recovery_codes(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/setup", json={"password": "pw12345-secure"}, headers=h)
    secret = r.json()["secret"]
    # advance to the next time-step to avoid the anti-replay collision with confirm
    code = pyotp.TOTP(secret).now()
    await api_client.post("/api/me/mfa/confirm", json={"code": code}, headers=h)
    rgen = await api_client.post(
        "/api/me/mfa/recovery/regenerate", json={"password": "pw12345-secure"}, headers=h
    )
    assert rgen.status_code == 200, rgen.text
    assert len(rgen.json()["recovery_codes"]) == 10


async def test_me_exposes_mfa_setup_required_false_for_full(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    r = await api_client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["mfa_setup_required"] is False

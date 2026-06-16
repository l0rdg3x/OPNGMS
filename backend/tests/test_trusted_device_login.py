import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from webauthn.helpers import bytes_to_base64url

from app.api import auth as auth_api
from app.core import crypto
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa import UserMfa
from app.models.webauthn_credential import WebAuthnCredential
from app.services import mfa as mfa_svc
from app.services.app_settings import set_trusted_device_enabled, set_webauthn_settings
from tests.conftest import csrf_headers
from tests.factories import make_user

_CRED_ID = b"\xaa\xbb\xcc\xdd\x01\x02"
_CRED_ID_B64 = bytes_to_base64url(_CRED_ID)


async def _seed_totp_user(db_engine, email="tt@x.io", password="pw12345-secure"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password=password)
        secret = mfa_svc.new_secret()
        s.add(UserMfa(user_id=u.id, enabled=True, totp_secret_enc=crypto.encrypt(secret)))
        await s.commit()
        return u.id, secret


async def test_totp_remember_device_creates_row_and_cookie(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine)
    await api_client.post("/api/login", json={"email": "tt@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    code = pyotp.TOTP(secret).now()
    r = await api_client.post(
        "/api/login/mfa", json={"code": code, "remember_device": True}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert api_client.cookies.get("opngms_trusted_device")  # cookie set
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).scalars().all()
        assert len(rows) == 1


async def test_totp_without_remember_sets_no_cookie(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine, email="nt@x.io")
    await api_client.post("/api/login", json={"email": "nt@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post(
        "/api/login/mfa", json={"code": pyotp.TOTP(secret).now()}, headers=h
    )
    assert r.status_code == 200, r.text
    assert not api_client.cookies.get("opngms_trusted_device")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def test_remember_device_ignored_when_toggle_off(api_client, db_engine):
    uid, secret = await _seed_totp_user(db_engine, email="off@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_trusted_device_enabled(s, False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "off@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post(
        "/api/login/mfa", json={"code": pyotp.TOTP(secret).now(), "remember_device": True}, headers=h
    )
    assert r.status_code == 200, r.text
    assert not api_client.cookies.get("opngms_trusted_device")
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def _seed_passkey_user(db_engine, email="pkrd@x.io", password="pw12345-secure", sign_count=3):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_webauthn_settings(s, rp_id="opngms.test", rp_name="OPNGMS",
                                    origin="https://opngms.test")
        u = await make_user(s, email=email, password=password)
        s.add(WebAuthnCredential(
            user_id=u.id, credential_id=_CRED_ID, public_key=b"PUBKEY",
            sign_count=sign_count, name="key"))
        await s.commit()
        return u.id


async def test_webauthn_remember_device_creates_row_and_cookie(api_client, db_engine, monkeypatch):
    uid = await _seed_passkey_user(db_engine)
    await api_client.post("/api/login", json={"email": "pkrd@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/login/webauthn/begin", headers=h)
    monkeypatch.setattr(auth_api.wa, "verify_authentication", lambda **k: 4)
    r = await api_client.post(
        "/api/login/webauthn/complete",
        json={"credential": {"id": _CRED_ID_B64, "rawId": _CRED_ID_B64}, "remember_device": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert api_client.cookies.get("opngms_trusted_device")  # cookie set on the webauthn path too
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).scalars().all()
        assert len(rows) == 1

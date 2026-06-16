from datetime import UTC, datetime, timedelta

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
from app.services.app_settings import (
    set_mfa_policy,
    set_trusted_device_enabled,
    set_webauthn_settings,
)
from app.services.trusted_device import TrustedDeviceService
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


async def _mint_trusted_cookie(db_engine, user_id, days=30):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        _, raw = await TrustedDeviceService(s).create_for_user(
            user_id, days=days, user_agent="UA", ip="1.2.3.4"
        )
        await s.commit()
        return raw


async def test_login_skips_mfa_with_valid_trusted_cookie(api_client, db_engine):
    uid, _ = await _seed_totp_user(db_engine, email="skip@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid)
    api_client.cookies.set("opngms_trusted_device", raw)
    r = await api_client.post("/api/login", json={"email": "skip@x.io", "password": "pw12345-secure"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"  # second factor skipped
    # a full session: an app endpoint works
    assert (await api_client.get("/api/me")).status_code == 200


async def test_login_requires_mfa_without_cookie(api_client, db_engine):
    await _seed_totp_user(db_engine, email="nocookie@x.io")
    r = await api_client.post("/api/login", json={"email": "nocookie@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"
    assert r.json()["remember_device"]["enabled"] is True
    assert r.json()["remember_device"]["days"] == 30


async def test_login_requires_mfa_with_other_users_cookie(api_client, db_engine):
    uid_a, _ = await _seed_totp_user(db_engine, email="owner@x.io")
    await _seed_totp_user(db_engine, email="victim@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid_a)  # owner's cookie
    api_client.cookies.set("opngms_trusted_device", raw)
    r = await api_client.post("/api/login", json={"email": "victim@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"  # cookie belongs to a different user


async def test_login_requires_mfa_when_toggle_off_even_with_cookie(api_client, db_engine):
    uid, _ = await _seed_totp_user(db_engine, email="togoff@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid)
    api_client.cookies.set("opngms_trusted_device", raw)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_trusted_device_enabled(s, False)
        await s.commit()
    r = await api_client.post("/api/login", json={"email": "togoff@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"
    assert r.json()["remember_device"]["enabled"] is False


async def test_login_requires_mfa_with_expired_cookie(api_client, db_engine):
    uid, _ = await _seed_totp_user(db_engine, email="exp@x.io")
    raw = await _mint_trusted_cookie(db_engine, uid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        row = (
            await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)  # force expiry
        await s.commit()
    api_client.cookies.set("opngms_trusted_device", raw)
    r = await api_client.post("/api/login", json={"email": "exp@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"  # an expired trusted cookie does not skip


async def test_trusted_cookie_does_not_bypass_mandatory_enrollment(api_client, db_engine):
    # A non-enrolled user under policy "all" must still be forced into mfa_setup — a trusted cookie
    # (even one minted for this very user) can never skip MANDATORY enrollment.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="noenroll@x.io", password="pw12345-secure")
        await set_mfa_policy(s, "all")
        await s.commit()
        uid = u.id
    raw = await _mint_trusted_cookie(db_engine, uid)
    api_client.cookies.set("opngms_trusted_device", raw)
    r = await api_client.post(
        "/api/login", json={"email": "noenroll@x.io", "password": "pw12345-secure"}
    )
    assert r.json()["status"] == "mfa_setup_required"  # trusted cookie can't skip enrollment

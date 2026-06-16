from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from webauthn.helpers import bytes_to_base64url

from app.api import auth as auth_api
from app.models.webauthn_credential import WebAuthnCredential
from app.services.app_settings import set_webauthn_settings
from tests.conftest import csrf_headers
from tests.factories import make_user

_CRED_ID = b"\xde\xad\xbe\xef\x01\x02"
_CRED_ID_B64 = bytes_to_base64url(_CRED_ID)


async def _seed_passkey_user(db_engine, email="pk@x.io", password="pw12345-secure", sign_count=3):
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


async def test_login_with_only_passkey_requires_mfa_with_webauthn_method(api_client, db_engine):
    await _seed_passkey_user(db_engine)
    r = await api_client.post("/api/login", json={"email": "pk@x.io", "password": "pw12345-secure"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "mfa_required"
    assert r.json()["methods"] == ["webauthn"]
    # the pending session cannot reach a normal app endpoint
    assert (await api_client.get("/api/sessions")).status_code == 403


async def test_login_webauthn_begin_returns_options(api_client, db_engine):
    await _seed_passkey_user(db_engine)
    await api_client.post("/api/login", json={"email": "pk@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post("/api/login/webauthn/begin", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "challenge" in body and body["rpId"] == "opngms.test"
    assert any(c["id"] == _CRED_ID_B64 for c in body.get("allowCredentials", []))


async def test_login_webauthn_complete_mints_full_session(api_client, db_engine, monkeypatch):
    uid = await _seed_passkey_user(db_engine, sign_count=3)
    await api_client.post("/api/login", json={"email": "pk@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/login/webauthn/begin", headers=h)
    # stub the assertion verification -> a higher sign count
    monkeypatch.setattr(auth_api.wa, "verify_authentication", lambda **k: 4)
    r = await api_client.post(
        "/api/login/webauthn/complete",
        json={"credential": {"id": _CRED_ID_B64, "rawId": _CRED_ID_B64}},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["user"]["email"] == "pk@x.io"
    # now a full session: app endpoint works
    assert (await api_client.get("/api/me")).status_code == 200
    # sign count bumped + last_used_at set + challenge cleared
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cred = (
            await s.execute(select(WebAuthnCredential).where(WebAuthnCredential.user_id == uid))
        ).scalar_one()
        assert cred.sign_count == 4
        assert cred.last_used_at is not None


async def test_login_webauthn_complete_rejects_bad_assertion(api_client, db_engine, monkeypatch):
    await _seed_passkey_user(db_engine)
    await api_client.post("/api/login", json={"email": "pk@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/login/webauthn/begin", headers=h)

    def _boom(**k):
        raise auth_api.wa.WebAuthnError("nope")

    monkeypatch.setattr(auth_api.wa, "verify_authentication", _boom)
    r = await api_client.post(
        "/api/login/webauthn/complete",
        json={"credential": {"id": _CRED_ID_B64, "rawId": _CRED_ID_B64}},
        headers=h,
    )
    assert r.status_code == 401, r.text
    # the single-use challenge was burned: a retry sees no pending challenge
    r2 = await api_client.post(
        "/api/login/webauthn/complete",
        json={"credential": {"id": _CRED_ID_B64, "rawId": _CRED_ID_B64}},
        headers=h,
    )
    assert r2.status_code == 400


async def test_login_webauthn_begin_409_when_unconfigured(api_client, db_engine):
    # a user with a passkey but the RP config cleared -> begin is 409
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="nc@x.io", password="pw12345-secure")
        s.add(WebAuthnCredential(
            user_id=u.id, credential_id=b"\x01\x02", public_key=b"PK", sign_count=0, name="k"))
        await s.commit()
    await api_client.post("/api/login", json={"email": "nc@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post("/api/login/webauthn/begin", headers=h)
    assert r.status_code == 409, r.text


async def test_login_with_totp_and_passkey_lists_both_methods(api_client, db_engine):
    import pyotp

    from app.core import crypto
    from app.models.user_mfa import UserMfa
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_webauthn_settings(s, rp_id="opngms.test", rp_name="OPNGMS",
                                    origin="https://opngms.test")
        u = await make_user(s, email="both@x.io", password="pw12345-secure")
        s.add(UserMfa(user_id=u.id, enabled=True, totp_secret_enc=crypto.encrypt(pyotp.random_base32())))
        s.add(WebAuthnCredential(
            user_id=u.id, credential_id=b"\x09\x08", public_key=b"PK", sign_count=0, name="k"))
        await s.commit()
    r = await api_client.post(
        "/api/login", json={"email": "both@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_required"
    assert set(r.json()["methods"]) == {"totp", "webauthn"}

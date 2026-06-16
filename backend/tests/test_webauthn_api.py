import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import mfa as mfa_api
from app.models.session import Session
from app.services.app_settings import set_mfa_policy, set_webauthn_settings
from tests.conftest import csrf_headers
from tests.factories import make_user


class _StubVerifiedReg:
    """Mimics py_webauthn's VerifiedRegistration return object (only the fields we persist)."""

    def __init__(self, *, credential_id=b"\x11\x22\x33", public_key=b"PUBKEY",
                 sign_count=0, aaguid="00000000-0000-0000-0000-000000000000"):
        self.credential_id = credential_id
        self.credential_public_key = public_key
        self.sign_count = sign_count
        self.aaguid = aaguid


async def _seed_user(db_engine, email="w@x.io", password="pw12345-secure", is_superadmin=False):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password=password, is_superadmin=is_superadmin)
        await s.commit()
        return u.id


async def _configure_rp(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_webauthn_settings(s, rp_id="opngms.test", rp_name="OPNGMS",
                                    origin="https://opngms.test")
        await s.commit()


async def _login(api_client, email="w@x.io", password="pw12345-secure"):
    r = await api_client.post("/api/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


async def test_register_begin_409_when_unconfigured(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    assert r.status_code == 409, r.text
    assert "not configured" in r.json()["detail"].lower()


async def test_register_begin_requires_password_step_up(api_client, db_engine):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    # Wrong password is refused before any challenge is minted (step-up, like TOTP /me/mfa/setup).
    r = await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h,
                              json={"password": "wrong-password"})
    assert r.status_code in (401, 403, 422), r.text


async def test_register_begin_returns_options_and_persists_challenge(api_client, db_engine):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "challenge" in body and body["rp"]["id"] == "opngms.test"
    # the challenge is persisted on the session row (single-use, server-side)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        sess = (
            await s.execute(select(Session).where(Session.kind == "full"))
        ).scalar_one()
        assert sess.webauthn_challenge == body["challenge"]


async def test_register_complete_creates_credential(api_client, db_engine, monkeypatch):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    r = await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}, "name": "My Key"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "My Key"
    # never serialize key bytes / credential_id
    assert "public_key" not in out and "credential_id" not in out


async def test_register_complete_flips_setup_session_to_full(api_client, db_engine, monkeypatch):
    # policy "all" + no MFA -> login issues an mfa_setup session; a passkey enrolls -> full.
    await _seed_user(db_engine, email="setup@x.io")
    await _configure_rp(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_mfa_policy(s, "all")
        await s.commit()
    r = await api_client.post(
        "/api/login", json={"email": "setup@x.io", "password": "pw12345-secure"})
    assert r.json()["status"] == "mfa_setup_required"
    h = csrf_headers(api_client)
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    rc = await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}}, headers=h)
    assert rc.status_code == 200, rc.text
    # session is now full: a normal app endpoint works
    me = await api_client.get("/api/sessions")
    assert me.status_code == 200


async def test_list_credentials_hides_key_material(api_client, db_engine, monkeypatch):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}, "name": "K1"}, headers=h)
    lst = await api_client.get("/api/me/mfa/webauthn/credentials")
    assert lst.status_code == 200, lst.text
    rows = lst.json()
    assert len(rows) == 1 and rows[0]["name"] == "K1"
    assert "public_key" not in rows[0] and "credential_id" not in rows[0]


async def test_delete_credential(api_client, db_engine, monkeypatch):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    created = (await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}}, headers=h)).json()
    cid = created["id"]
    d = await api_client.delete(f"/api/me/mfa/webauthn/credentials/{cid}", headers=h)
    assert d.status_code == 204, d.text
    lst = await api_client.get("/api/me/mfa/webauthn/credentials")
    assert lst.json() == []


async def test_delete_unknown_is_404(api_client, db_engine):
    await _seed_user(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    d = await api_client.delete(
        f"/api/me/mfa/webauthn/credentials/{uuid.uuid4()}", headers=h)
    assert d.status_code == 404


async def test_delete_last_factor_guard_blocks_when_policy_requires(
    api_client, db_engine, monkeypatch
):
    # policy "all": the passkey is the user's only factor -> delete must be refused (409).
    await _seed_user(db_engine, email="guard@x.io")
    await _configure_rp(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_mfa_policy(s, "all")
        await s.commit()
    # log in (mfa_setup) and register a passkey -> session becomes full
    await api_client.post(
        "/api/login", json={"email": "guard@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    created = (await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}}, headers=h)).json()
    cid = created["id"]
    d = await api_client.delete(f"/api/me/mfa/webauthn/credentials/{cid}", headers=h)
    assert d.status_code == 409, d.text
    assert "last mfa factor" in d.json()["detail"].lower()


async def test_status_block_exposes_configured_and_count(api_client, db_engine, monkeypatch):
    await _seed_user(db_engine)
    await _configure_rp(db_engine)
    await _login(api_client)
    h = csrf_headers(api_client)
    st0 = await api_client.get("/api/me/mfa")
    assert st0.status_code == 200, st0.text
    assert st0.json()["webauthn"] == {"configured": True, "credentials": 0}
    await api_client.post("/api/me/mfa/webauthn/register/begin", headers=h, json={"password": "pw12345-secure"})
    monkeypatch.setattr(mfa_api.wa, "verify_registration", lambda **k: _StubVerifiedReg())
    await api_client.post(
        "/api/me/mfa/webauthn/register/complete",
        json={"credential": {"id": "abc"}}, headers=h)
    st1 = await api_client.get("/api/me/mfa")
    assert st1.json()["webauthn"]["credentials"] == 1

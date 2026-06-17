import inspect
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.smtp import oauth_callback
from app.core import crypto
from app.core.config import Settings, assert_secure_secrets, get_settings
from app.models.smtp_settings import SmtpSettings  # noqa: F401
from app.services.email import oauth as oauth_svc
from app.services.smtp_settings import SmtpSettingsService
from tests.factories import make_user


def test_callback_audits_inline():
    # The callback is a mutating GET, so the POST/PUT/PATCH/DELETE audit-coverage guard does not see it;
    # assert directly that it audits smtp.oauth.connected so a future refactor can't silently drop it.
    src = inspect.getsource(oauth_callback)
    assert ".record(" in src and "smtp.oauth.connected" in src


def test_public_base_url_must_be_https(monkeypatch):
    # The redirect URI carries the OAuth authorization code -> it must be https://.
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://insecure.example")
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        assert_secure_secrets(Settings())
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ok.example")
    assert_secure_secrets(Settings())  # https:// -> no raise
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert_secure_secrets(Settings())  # empty -> feature off, no raise


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        sa = await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await make_user(s, email="reg@x.io", password="pw12345-secure")
        await s.commit()
        return sa.id


async def _login(api_client, email="sa@x.io"):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})


async def _save_creds(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await SmtpSettingsService(s).upsert(
            enabled=True, host="smtp.x", port=587, security="starttls", username=None,
            from_email="f@x.io", from_name="F", password=None, clear_password=False,
            auth_method="oauth", oauth_provider="google", oauth_client_id="cid.apps",
            oauth_client_secret="secret", oauth_tenant_id=None)
        await s.commit()


async def test_authorize_requires_public_base_url(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 409


async def test_authorize_unknown_provider_404(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/nope/authorize")
    assert r.status_code == 404


async def test_authorize_returns_url_for_superadmin(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == ["cid.apps"]
    assert q["redirect_uri"] == ["https://opngms.test/api/admin/smtp/oauth/google/callback"]
    assert "state" in q


async def test_authorize_forbidden_for_non_superadmin(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client, "reg@x.io")
    r = await api_client.get("/api/admin/smtp/oauth/google/authorize")
    assert r.status_code == 403


@respx.mock
async def test_callback_stores_refresh_and_redirects(api_client, db_engine, monkeypatch):
    uid = await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    state = oauth_svc.sign_state(uid, "google")
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-new", "access_token": "at"}))
    r = await api_client.get(
        f"/api/admin/smtp/oauth/google/callback?code=auth-code&state={state}",
        follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://opngms.test/admin/smtp?oauth=success"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        stored = await SmtpSettingsService(s).get()
        assert stored.oauth_refresh_token_enc is not None
        assert crypto.decrypt(stored.oauth_refresh_token_enc) == "rt-new"


async def test_callback_bad_state_redirects_error_no_write(api_client, db_engine, monkeypatch):
    await _seed(db_engine)
    await _save_creds(db_engine)
    monkeypatch.setattr(get_settings(), "public_base_url", "https://opngms.test", raising=False)
    await _login(api_client)
    r = await api_client.get(
        "/api/admin/smtp/oauth/google/callback?code=c&state=forged.state", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "https://opngms.test/admin/smtp?oauth=error"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        stored = await SmtpSettingsService(s).get()
        assert stored.oauth_refresh_token_enc is None

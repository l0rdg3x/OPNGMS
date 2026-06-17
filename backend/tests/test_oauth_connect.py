from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.services.email.oauth import (
    OAuthTokenError,
    build_authorize_url,
    exchange_code,
    sign_state,
    verify_state,
)

_RID = "11111111-1111-1111-1111-111111111111"


def test_authorize_url_google():
    url = build_authorize_url("google", "cid.apps", "https://h/cb", "STATE", None)
    p = urlparse(url)
    q = parse_qs(p.query)
    assert p.scheme == "https" and p.netloc == "accounts.google.com"
    assert q["response_type"] == ["code"] and q["client_id"] == ["cid.apps"]
    assert q["redirect_uri"] == ["https://h/cb"] and q["state"] == ["STATE"]
    assert q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    assert q["scope"] == ["https://mail.google.com/"]


def test_authorize_url_microsoft_tenant_in_path():
    url = build_authorize_url("microsoft", "cid", "https://h/cb", "ST", "my-tenant")
    p = urlparse(url)
    assert p.netloc == "login.microsoftonline.com"
    assert p.path == "/my-tenant/oauth2/v2.0/authorize"
    q = parse_qs(p.query)
    assert q["scope"] == ["https://outlook.office365.com/SMTP.Send offline_access"]


def test_authorize_url_rejects_unknown_provider_and_unsafe_tenant():
    with pytest.raises(OAuthTokenError):
        build_authorize_url("nope", "c", "https://h/cb", "S", None)
    with pytest.raises(OAuthTokenError):
        build_authorize_url("microsoft", "c", "https://h/cb", "S", "../evil")


def test_state_roundtrip_and_rejections():
    s = sign_state(_RID, "google")
    assert verify_state(s, _RID, "google") is True
    assert verify_state(s, _RID, "microsoft") is False
    assert verify_state(s, "22222222-2222-2222-2222-222222222222", "google") is False
    assert verify_state(s + "x", _RID, "google") is False
    assert verify_state("not.a.state", _RID, "google") is False
    past = datetime.now(UTC) - timedelta(minutes=20)
    s_old = sign_state(_RID, "google", now=past)
    assert verify_state(s_old, _RID, "google") is False


@respx.mock
async def test_exchange_code_returns_refresh_and_access():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"refresh_token": "rt-123", "access_token": "at-123"}))
    out = await exchange_code("google", "cid", "secret", "auth-code", "https://h/cb", None)
    assert out == {"refresh_token": "rt-123", "access_token": "at-123"}


@respx.mock
async def test_exchange_code_raises_on_error_and_missing_refresh():
    respx.post("https://oauth2.googleapis.com/token").mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(OAuthTokenError):
        await exchange_code("google", "cid", "secret", "bad", "https://h/cb", None)
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "at-only"}))
    with pytest.raises(OAuthTokenError):
        await exchange_code("google", "cid", "secret", "code", "https://h/cb", None)

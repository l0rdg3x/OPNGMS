import httpx
import pytest
import respx

from app.services.email.oauth import OAuthTokenError, fetch_access_token


@respx.mock
async def test_google_refresh_returns_access_token():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ya29.tok", "expires_in": 3599}))
    tok = await fetch_access_token("google", "cid", "secret", "refresh")
    assert tok == "ya29.tok"


@respx.mock
async def test_microsoft_uses_tenant_in_url():
    route = respx.post("https://login.microsoftonline.com/my-tenant/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ms.tok"}))
    tok = await fetch_access_token("microsoft", "cid", "secret", "refresh", tenant_id="my-tenant")
    assert tok == "ms.tok" and route.called


@respx.mock
async def test_microsoft_defaults_tenant_common():
    route = respx.post("https://login.microsoftonline.com/common/oauth2/v2.0/token").mock(
        return_value=httpx.Response(200, json={"access_token": "ms.tok"}))
    await fetch_access_token("microsoft", "cid", "secret", "refresh")
    assert route.called


@respx.mock
async def test_error_response_raises_oauth_token_error():
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(OAuthTokenError):
        await fetch_access_token("google", "cid", "secret", "refresh")


async def test_unknown_provider_raises():
    with pytest.raises(OAuthTokenError):
        await fetch_access_token("yahoo", "cid", "secret", "refresh")

"""Exchange a stored OAuth2 refresh token for a short-lived access token, for SMTP XOAUTH2.

The token endpoints are FIXED per-provider constants (no user-controlled host), so this adds no
outbound SSRF surface. The access token is returned in memory only and is never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from app.core.config import get_settings

_TOKEN_URL = {
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
}
# The only user-provided value interpolated into the request URL is the Microsoft tenant (a GUID, a
# verified domain, or the literal "common"/"organizations"). Guard it AT THE SINK — re-validated here
# even though the API schema already checks it — so no path traversal / endpoint probing can ride the
# request URL (sanitizes the py/partial-ssrf sink across the settings DB round-trip).
_SAFE_TENANT = re.compile(r"\A[A-Za-z0-9._-]{1,128}\Z")
# Scope echoed on the refresh grant (Microsoft wants it; Google ignores an extra scope harmlessly).
_SCOPE = {
    "google": "https://mail.google.com/",
    "microsoft": "https://outlook.office365.com/SMTP.Send offline_access",
}
_TIMEOUT = 15.0


class OAuthTokenError(Exception):
    """A refresh-token exchange failed. Message is safe to surface; carries no token material."""


async def fetch_access_token(
    provider: str, client_id: str, client_secret: str, refresh_token: str,
    tenant_id: str | None = None,
) -> str:
    if provider not in _TOKEN_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    tenant = tenant_id or "common"
    if not _SAFE_TENANT.match(tenant):
        raise OAuthTokenError("invalid tenant id")
    url = _TOKEN_URL[provider].format(tenant=tenant)
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "scope": _SCOPE[provider],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(url, data=data)
    except httpx.HTTPError as exc:
        raise OAuthTokenError("token endpoint unreachable") from exc
    if resp.status_code != 200:
        # Body may name the error class (e.g. invalid_grant) but never the token.
        raise OAuthTokenError(f"token exchange failed ({resp.status_code})")
    token = resp.json().get("access_token")
    if not token:
        raise OAuthTokenError("no access_token in response")
    return token


_AUTHORIZE_URL = {
    "google": "https://accounts.google.com/o/oauth2/v2/auth",
    "microsoft": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
}
_STATE_TTL = timedelta(minutes=10)


def build_authorize_url(
    provider: str, client_id: str, redirect_uri: str, state: str, tenant_id: str | None
) -> str:
    """The user-facing consent URL. Fixed per-provider host; the only interpolated user value is the
    Microsoft tenant (sink-guarded), so this adds no SSRF / open-redirect surface."""
    if provider not in _AUTHORIZE_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    tenant = tenant_id or "common"
    if not _SAFE_TENANT.match(tenant):
        raise OAuthTokenError("invalid tenant id")
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE[provider],
        "state": state,
    }
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    else:
        params["response_mode"] = "query"
    base = _AUTHORIZE_URL[provider].format(tenant=tenant)
    return f"{base}?{urlencode(params)}"


async def exchange_code(
    provider: str, client_id: str, client_secret: str, code: str, redirect_uri: str,
    tenant_id: str | None,
) -> dict:
    """Exchange an authorization code for {refresh_token, access_token}. Raises OAuthTokenError on
    failure or a missing refresh token (no token material in the message)."""
    if provider not in _TOKEN_URL:
        raise OAuthTokenError(f"unsupported oauth provider: {provider}")
    tenant = tenant_id or "common"
    if not _SAFE_TENANT.match(tenant):
        raise OAuthTokenError("invalid tenant id")
    url = _TOKEN_URL[provider].format(tenant=tenant)
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE[provider],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as http:
            resp = await http.post(url, data=data)
    except httpx.HTTPError as exc:
        raise OAuthTokenError("token endpoint unreachable") from exc
    if resp.status_code != 200:
        raise OAuthTokenError(f"code exchange failed ({resp.status_code})")
    body = resp.json()
    refresh = body.get("refresh_token")
    if not refresh:
        raise OAuthTokenError("no refresh_token in response")
    return {"refresh_token": refresh, "access_token": body.get("access_token")}


def _state_sig(payload: str) -> str:
    return hmac.new(get_settings().session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def sign_state(user_id: object, provider: str, *, now: datetime | None = None) -> str:
    """A stateless, signed, expiring CSRF state bound to (user, provider)."""
    exp = int(((now or datetime.now(UTC)) + _STATE_TTL).timestamp())
    nonce = secrets.token_urlsafe(8)
    payload = f"{user_id}.{provider}.{exp}.{nonce}"
    return f"{payload}.{_state_sig(payload)}"


def verify_state(state: str, user_id: object, provider: str, *, now: datetime | None = None) -> bool:
    parts = state.rsplit(".", 1)
    if len(parts) != 2:
        return False
    payload, sig = parts
    if not hmac.compare_digest(sig, _state_sig(payload)):
        return False
    fields = payload.split(".")
    if len(fields) != 4:
        return False
    s_user, s_provider, s_exp, _nonce = fields
    if s_user != str(user_id) or s_provider != provider:
        return False
    try:
        exp = int(s_exp)
    except ValueError:
        return False
    return (now or datetime.now(UTC)).timestamp() < exp

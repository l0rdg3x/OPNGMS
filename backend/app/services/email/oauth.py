"""Exchange a stored OAuth2 refresh token for a short-lived access token, for SMTP XOAUTH2.

The token endpoints are FIXED per-provider constants (no user-controlled host), so this adds no
outbound SSRF surface. The access token is returned in memory only and is never logged.
"""
from __future__ import annotations

import re

import httpx

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

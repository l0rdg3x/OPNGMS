# OAuth2 (XOAUTH2) authentication for the SMTP relay — Gmail + Microsoft 365

**Date:** 2026-06-16
**Status:** design approved (brainstorm) — ready for implementation plan

## Problem

OPNGMS sends scheduled/on-demand report email through one global SMTP relay (`SmtpSettings` singleton,
`app/services/email/smtp.py`), authenticated only with **username + password**. Google Workspace and
Microsoft 365 are **disabling basic-auth SMTP**, so an MSP using Gmail/M365 as the relay must authenticate
with **OAuth2 (SASL XOAUTH2)**: exchange a long-lived **refresh token** for a short-lived **access token**
at send time and `AUTH XOAUTH2`.

## Goal

Add OAuth2 as an alternative auth method for the SMTP relay, for **Gmail** and **Microsoft 365**, keeping
password auth working unchanged. Support **both** ways to get the refresh token into OPNGMS over one shared
core (the user confirmed both): **(2) manual entry** (universal — works on any self-hosted deployment) and
**(1) a "Connect" button** (auth-code + callback — nicer UX where the console has a public callback URL).
Build the shared core + manual entry first (PR1), then the Connect button on top (PR2).

## Shared core

Whatever produced the refresh token, OPNGMS stores the same fields and runs the same send-time path:
`refresh_token → access_token (provider token endpoint) → SMTP AUTH XOAUTH2`.

### Data model — extend `SmtpSettings` (`app/models/smtp_settings.py`, + migration)
Add nullable columns (defaults keep existing rows = password auth):
- `auth_method: str` — `"password"` (default/server_default) | `"oauth"`.
- `oauth_provider: str | None` — `"google"` | `"microsoft"`.
- `oauth_client_id: str | None`.
- `oauth_client_secret_enc: bytes | None` — Fernet (`MASTER_KEY`).
- `oauth_refresh_token_enc: bytes | None` — Fernet.
- `oauth_tenant_id: str | None` — the Azure AD tenant for Microsoft (`"common"` if unset); ignored for Google.

### Token service — `app/services/email/oauth.py` (new)
```python
_TOKEN_URL = {
    "google": "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
}
# Scopes requested on the refresh-token grant (some providers require the scope echoed).
_SCOPE = {"google": "https://mail.google.com/",
          "microsoft": "https://outlook.office365.com/SMTP.Send offline_access"}

class OAuthTokenError(Exception): ...

async def fetch_access_token(provider, client_id, client_secret, refresh_token, tenant_id=None) -> str:
    """POST grant_type=refresh_token to the provider's fixed token endpoint; return the access_token.
    Provider hostnames are hard-coded constants (no user-controlled host -> no SSRF). Raises
    OAuthTokenError on any failure; the access token is never logged."""
```
- Uses a short-lived `httpx.AsyncClient` (HTTPS, no redirects). The endpoint host is a fixed constant per
  provider — **not** user-controlled — so this is outside the device SSRF guard surface by construction.
- Returns the bare access token. (Optional small in-process cache keyed by refresh-token hash + expiry; PR1
  may fetch per send for simplicity — a report run sends few mails. Decide in the plan; default: no cache.)

### Send path — XOAUTH2 (`app/services/email/smtp.py`)
- Extend `SmtpSendConfig` with `access_token: str | None = None` (and keep `password`).
- `send_email`: when `access_token` is set, authenticate with **`aiosmtplib`'s built-in XOAUTH2** (verified
  present: `aiosmtplib 5.1.1` has `SMTP.auth_xoauth2`). Use the low-level client rather than the `send`
  helper:
  ```python
  client = aiosmtplib.SMTP(hostname=cfg.host, port=cfg.port,
                           start_tls=cfg.security == "starttls", use_tls=cfg.security == "tls")
  async with client:
      if cfg.access_token:
          await client.auth_xoauth2(cfg.username, cfg.access_token)
      elif cfg.username:
          await client.login(cfg.username, cfg.password or "")
      await client.send_message(message)
  ```
  (`async with client` connects + (start)TLS handshakes + quits; `auth_xoauth2(user, token)` builds the
  SASL `user=..\x01auth=Bearer ..\x01\x01` string.) Errors still map to `EmailSendError` via
  `_safe_smtp_error` (which already redacts AUTH detail).

### Settings service — `app/services/smtp_settings.py`
- `upsert(...)` gains the oauth params with the same **keep/clear** convention as the password
  (`oauth_client_secret`/`oauth_refresh_token` + `clear_*` flags; `None` + not-clear ⇒ keep existing). It
  encrypts the two secrets with `crypto.encrypt`.
- Replace the sync `to_send_config` with an **async** `resolve_send_config(row) -> SmtpSendConfig`:
  - `auth_method == "oauth"`: decrypt client_secret + refresh_token, `await fetch_access_token(...)`, return
    a config with `username = from_email` (the OAuth account = the sender) and `access_token` set,
    `password=None`.
  - else: today's behaviour (decrypt password).
  Update the two callers (the report-delivery worker + the test-send endpoint) to `await` it.

### API — `app/api/smtp.py`
- `SmtpSettingsOut` gains `auth_method`, `oauth_provider`, `oauth_client_id`, `oauth_tenant_id`,
  `has_client_secret: bool`, `has_refresh_token: bool` (**never** return the secrets, mirroring
  `has_password`).
- `PUT` body gains the oauth fields + `clear_client_secret` / `clear_refresh_token`.
- `POST /test` resolves the send config via `resolve_send_config` (so a test-send exercises the OAuth path
  too), keeping the existing audit (`action="smtp.test"`). Superadmin-gated + CSRF as today.

### Frontend — `frontend/src/.../SmtpSettingsPage.tsx`
- An **Authentication** segmented control: *Password* | *OAuth2*. When OAuth2: a **Provider** select
  (Google / Microsoft 365), `Client ID`, `Client secret`, `Refresh token`, and `Tenant ID` (shown only for
  Microsoft). Secrets render as write-only (placeholder shows "saved" via the `has_*` flags; blank = keep).
- The existing **Send test** button works unchanged (the backend resolves OAuth).
- New UI strings added to `en.ts` + mirrored across all 12 locales (compiler-enforced parity).

## Security
- `oauth_client_secret` + `oauth_refresh_token` are Fernet-encrypted at rest (`MASTER_KEY`), **never**
  returned by the API and **never** logged (same rule as the SMTP password / device secrets). The fetched
  access token lives only in memory for the send and is never persisted or logged.
- The token endpoints are fixed per-provider constants — no user-supplied URL, so no new outbound SSRF
  surface. HTTPS, no redirects.

## Testing
- **Unit** (no network/box): `fetch_access_token` against a mocked token endpoint (respx) — success returns
  the token; an error body raises `OAuthTokenError`; the microsoft URL interpolates the tenant. The XOAUTH2
  branch of `send_email` (mock `aiosmtplib.SMTP` / `auth_xoauth2`) — asserts `auth_xoauth2(from_email, token)`
  is called and `login` is not. `SmtpSettingsService` round-trip: oauth upsert encrypts both secrets, the
  keep/clear flags behave, `resolve_send_config` returns an `access_token` config for oauth and a password
  config otherwise. API: `SmtpSettingsOut` exposes `has_client_secret`/`has_refresh_token` but not the values.
- **Frontend**: a test that selecting OAuth2 + Microsoft reveals the Tenant field and the PUT body carries
  the oauth fields; `npm run build` (tsc -b) + `npm test`.
- **Live (operator, needs your OAuth app)**: register an OAuth app at Google/Azure, obtain a refresh token,
  enter it in OPNGMS, and Send-test to a real Gmail/M365 mailbox. Documented as an operator step — it can't
  run in CI (needs real provider credentials).
- Gate: `cd backend && python -m pytest -q` + `ruff check app/` + `cd frontend && npm run build && npm test`.

## PR breakdown
- **PR1 — core + manual entry (this spec's body).** Model + migration, token service, XOAUTH2 send,
  settings service `resolve_send_config`, API + frontend manual OAuth form, tests. Fully usable: an admin
  who pastes client_id/secret/refresh_token can send via Gmail/M365 OAuth.
- **PR2 — "Connect" button (auth-code + callback).** Backend `GET …/smtp/oauth/{provider}/authorize`
  (redirect to the provider consent screen with a signed `state`) + `GET …/smtp/oauth/{provider}/callback`
  (validate `state`, exchange `code` for the refresh token, store it). Needs a configured public base URL +
  the OAuth app's redirect URI registered. Frontend: a "Connect <provider>" button that opens the authorize
  URL and reflects the connected state. Same stored fields + send path as PR1 — purely a nicer way to obtain
  the refresh token.

## Out of scope (PR1/PR2)
- Microsoft **Graph API** `sendMail` as an alternative transport (XOAUTH2 over SMTP is the smaller, consistent
  change; Graph could be a later option if M365 also removes SMTP AUTH).
- Per-tenant SMTP relays (the relay stays a global singleton).
- App-only / client-credentials (service-account) flows — the refresh-token (delegated) model covers both
  providers with one core; app-only can be a future addition.

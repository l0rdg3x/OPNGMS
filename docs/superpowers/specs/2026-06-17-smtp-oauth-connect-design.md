# SMTP OAuth "Connect" (PR2) — design

**Date:** 2026-06-17
**Part of:** OAuth2 SMTP relay (PR1 = manual refresh-token entry, shipped v0.19.0). This is **PR2**.
**Status:** approved, ready for plan.
**⚠️ Experimental / untested:** the browser consent + live-provider redirect cannot be verified in this
environment (no public callback URL, no registered OAuth app). The code is unit-tested with mocked HTTP;
the end-to-end flow against a real Google/Microsoft tenant is **untested** and the feature is labelled as
such in the UI, CHANGELOG, and docs.

## Goal

Replace the manual "paste a refresh token" step with a **"Connect with Google / Microsoft 365"** button
that runs the OAuth2 **authorization-code** flow and stores the obtained refresh token automatically.
The operator still registers the OAuth app and enters the **client id + client secret** manually; the
button only removes the manual refresh-token paste.

## Non-goals / out of scope

- Per-tenant SMTP OAuth (the SMTP relay stays a global superadmin singleton).
- Storing/registering the client id+secret *for* the operator — they still create the OAuth app and
  paste client id+secret (PR1 fields).
- PKCE (these are confidential clients with a client secret; the signed `state` covers CSRF).
- Refreshing/rotating the stored refresh token on a schedule (PR1's send-time exchange already mints
  short-lived access tokens from the refresh token).

## Architecture

The whole flow reuses PR1's encrypted storage and token machinery; PR2 adds only the authorize-URL
builder, the code→refresh-token exchange, a signed `state`, and the two HTTP routes + the button.

### Components

- **`backend/app/services/email/oauth.py` (extended)** — gains, alongside the existing
  `fetch_access_token`:
  - `build_authorize_url(provider, client_id, redirect_uri, state, tenant_id) -> str` — the user-facing
    consent URL. Google `https://accounts.google.com/o/oauth2/v2/auth` with
    `access_type=offline` + `prompt=consent` (forces a refresh token every time); Microsoft
    `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize` with `response_mode=query`. Scopes
    reuse the existing `_SCOPE`. The Microsoft tenant is sink-guarded by the existing `_SAFE_TENANT`.
  - `exchange_code(provider, client_id, client_secret, code, redirect_uri, tenant_id) -> dict` — POSTs to
    the existing `_TOKEN_URL` with `grant_type=authorization_code`; returns
    `{"refresh_token": ..., "access_token": ...}`. Raises `OAuthTokenError` (no token material in the
    message) on failure or a missing `refresh_token`.
  - `sign_state(user_id, provider, *, now=None) -> str` / `verify_state(state, user_id, provider, *,
    now=None) -> bool` — a **stateless** signed token: `f"{user_id}.{provider}.{exp}.{nonce}"` plus an
    `HMAC-SHA256(SESSION_SECRET, …)` signature, compared with `hmac.compare_digest`. TTL **10 minutes**.
    Binds the callback to the initiating superadmin + provider; expires; no server-side store.
- **`backend/app/core/config.py`** — new `public_base_url: str = ""` (env `PUBLIC_BASE_URL`, e.g.
  `https://opngms.example.com`). The redirect URI is
  `{public_base_url}/api/admin/smtp/oauth/{provider}/callback`; the post-flow landing is
  `{public_base_url}/admin/smtp?oauth=success|error`. Both are **server-built** — never client-supplied.
- **`backend/app/services/smtp_settings.py`** — new `store_oauth_refresh_token(provider, refresh_token)`:
  loads the existing singleton (must already hold client id+secret), sets `auth_method="oauth"`,
  `oauth_provider=provider`, encrypts the refresh token into `oauth_refresh_token_enc`, flushes. (Narrower
  than `upsert`, so the callback doesn't have to reconstruct every unrelated field.)
- **`backend/app/api/smtp.py`** — two new routes under the existing `/api/admin/smtp` router
  (superadmin via `require_org(Action.USER_MANAGE)`):
  - `GET /oauth/{provider}/authorize` → 404 unknown provider; 409 if `public_base_url` unset or the
    client id/secret aren't saved yet; else builds the signed `state` + the consent URL (using the stored
    client id + the saved tenant) and returns `{ "authorize_url": ... }`. Read-only (no DB write).
  - `GET /oauth/{provider}/callback?code=&state=` → the provider's browser redirect. Verifies `state`
    (HMAC + TTL + provider + `user == session`), exchanges `code` → refresh token using the stored
    client id+secret, stores it, audits `smtp.oauth.connected`, then **302-redirects** to
    `{public_base_url}/admin/smtp?oauth=success`. Any failure (denied consent, bad/expired state, missing
    creds, exchange error) → 302 to `…?oauth=error` (no token material leaks; the failure is logged).
- **Frontend** `frontend/src/pages/SmtpSettingsPage.tsx` + `frontend/src/admin/smtpHooks.ts` — in the
  OAuth branch, a **"Connect with …"** button with an **"Experimental — untested"** badge + a note. On
  click it GETs the authorize URL and sets `window.location.href` to it. On return the page reads
  `?oauth=success|error`, shows a toast, refetches settings, and cleans the query string. i18n ×13.

### Data flow (happy path)

1. Operator saves client id + secret (PR1 PUT) and sets `PUBLIC_BASE_URL`.
2. Clicks **Connect with Google** → SPA GETs `…/oauth/google/authorize` → `{authorize_url}` → SPA sets
   `window.location.href = authorize_url`.
3. Google shows consent; on approval redirects the browser to
   `{public_base_url}/api/admin/smtp/oauth/google/callback?code=…&state=…`.
4. The callback (superadmin session via the Lax cookie) verifies `state`, exchanges `code` → refresh
   token, `store_oauth_refresh_token("google", token)`, audits, 302 → `…/admin/smtp?oauth=success`.
5. The SPA shows "Account connected" and refetches (now `has_refresh_token=true`). The relay sends via
   PR1's send-time access-token exchange.

## Error handling

- Unknown provider → 404. `public_base_url` unset or client id/secret missing → 409 (authorize) with a
  clear message; the button shows a hint instead of firing.
- The callback never surfaces a raw error to the browser: every failure (consent denied, tampered/expired
  `state`, user mismatch, missing creds, exchange failure) becomes a 302 to `…?oauth=error`. The reason is
  logged server-side (no token material). The signed `state` is the callback's CSRF defence; a mutating
  GET callback is the standard OAuth pattern and is exempt from the POST/PUT/PATCH/DELETE audit-coverage
  guard, but the callback still audits `smtp.oauth.connected` on success.

## Security

- `state` is HMAC-signed (SESSION_SECRET), binds the initiating **superadmin + provider**, and expires in
  10 min; the callback also re-checks `state.user == session.user`. No open-redirect: `redirect_uri` and
  the landing URL are built from `public_base_url` (server config), never from a request parameter.
- Token endpoints + authorize endpoints are fixed per-provider constants; the only interpolated
  user-value is the Microsoft tenant, sink-guarded by the existing `_SAFE_TENANT`.
- The refresh token is Fernet-encrypted at rest (existing column); access tokens stay in memory; nothing
  token-bearing is logged or returned in a body. Routes are superadmin-gated.
- Adversarial security review after implementation (state forgery, cross-user callback, open-redirect,
  token leakage).

## Testing

Unit / API (mocked HTTP via `respx`, matching the existing `test_email_oauth.py` style):
- `build_authorize_url` — correct endpoint, `response_type=code`, scope, `state`, `access_type=offline`
  (Google), the Microsoft tenant path; rejects an unknown provider and an unsafe tenant.
- `sign_state`/`verify_state` — round-trips; rejects a tampered signature, an expired token, a
  provider mismatch, and a user mismatch.
- `exchange_code` — returns `{refresh_token, access_token}`; raises on non-200 and on a missing
  `refresh_token`.
- `GET …/authorize` — 404 unknown provider; 409 when `public_base_url` unset / creds missing; returns an
  `authorize_url` containing the expected params for a configured superadmin; 403 for a non-superadmin.
- `GET …/callback` — valid state + mocked exchange stores the refresh token and 302s to `?oauth=success`;
  bad/expired state → `?oauth=error` and no write; wrong-user state → error; unknown provider → 404;
  `public_base_url` unset → 409.
- Frontend (vitest+msw): the Connect button appears (with the experimental badge) when creds are saved;
  clicking it GETs the authorize URL; the `?oauth=success` return shows a toast + refetch. The button is
  gated/hinted when creds aren't saved yet.

**Explicitly untested (documented):** the real browser consent screen and the real provider redirect — they
need a public `PUBLIC_BASE_URL` and a registered OAuth app. This is what the "experimental — untested"
label communicates.

## Delivery

One PR (backend oauth/routes/config/service + frontend + i18n + docs). Tasks are TDD, subagent-driven.
README + CHANGELOG (marked **experimental**) + Wiki (Reporting/SMTP) updated; tag **v0.22.0** at the end.

# Remember-this-device (MFA sub-project B) — design

**Date:** 2026-06-16
**Part of:** MFA-future (sub-project A = WebAuthn passkeys, shipped v0.20.0; this is sub-project B).
**Status:** approved, ready for plan.

## Goal

After a user completes a second factor (TOTP or WebAuthn) at login, they may mark the current
device as *trusted*. On subsequent logins from that device the **password is still always
required**, but the **second factor is skipped** for a configurable number of days. Trust is
server-side and revocable.

## Non-goals / out of scope

- Skipping the password (this never weakens the first factor — only the second).
- Skipping mandatory MFA *enrollment*: a trusted cookie never bypasses a forced `mfa_setup`.
- Per-tenant configuration: the toggle and duration are org-wide (matches the existing MFA policy).
- Device fingerprinting / hard-binding to user-agent (UA is stored for display only, not enforced —
  UAs change and would silently break trust).
- Cross-user trust: a trusted cookie only ever applies to the user whose token_hash + user_id match.

## Architecture

A new `trusted_device` table records a per-(user, device) trust grant. The model mirrors the
existing session-token pattern exactly: a raw opaque token lives **only** in a browser cookie; the
database stores `token_hash = HMAC-SHA256(SESSION_SECRET, raw_token)`. This means a DB dump yields
no usable tokens, and rotating `SESSION_SECRET` invalidates all trusted devices (same property as
sessions). Trust is bound to `user_id`.

A new cookie `opngms_trusted_device` carries the raw token. At login, after the password verifies,
the server looks up the cookie's token_hash; if it maps to a non-expired, non-revoked row for the
authenticated user **and** the org toggle is on **and** the user is MFA-enrolled, the second factor
is skipped and a `full` session is minted directly.

### Components

- **Model** `backend/app/models/trusted_device.py` — `TrustedDevice`.
- **Service** `backend/app/services/trusted_device.py` — token mint/hash, `create_for_user`,
  `find_valid(user_id, raw_token)`, `touch` (update `last_used_at`), `list_for_user`,
  `revoke(id, user_id)`, `revoke_all(user_id)`, `purge_expired`. Reuses `SESSION_SECRET` via the
  same HMAC helper shape as `app/services/auth.py:_hash_token`.
- **Settings**
  - `app_settings.trusted_device_enabled` (org-wide on/off; default **on**) — getter/setter mirror
    `get_mfa_policy`/`set_mfa_policy`.
  - `runtime_settings.trusted_device_days` (int, default **30**, range 1–365, group
    `security_session`) — flows through the existing runtime-settings UI automatically.
- **Login API** `backend/app/api/auth.py`:
  - `/login`: after password verify, before deciding `kind`, attempt the trusted-device skip.
  - `/login/mfa` and `/login/webauthn/complete`: accept `remember_device: bool` in the body; on
    success, if the toggle is on, create a `trusted_device` row and set the cookie next to the
    session cookie.
  - `LoginOut` gains `remember_device: RememberDeviceInfo | None` (`{enabled: bool, days: int}`),
    populated when `status == "mfa_required"`.
- **Management API** `backend/app/api/trusted_devices.py` (prefix `/api`, full-session auth):
  - `GET /me/trusted-devices` → list (`id, user_agent, ip, created_at, last_used_at, expires_at`).
  - `DELETE /me/trusted-devices/{id}` → revoke one (scoped to caller; 404 if not theirs).
  - `DELETE /me/trusted-devices` → revoke all for the caller.
  - All mutations under `enforce_csrf` (same as other authenticated mutations).
- **Auto-revoke** — call `revoke_all(user_id)` from the two places a user's MFA factors are torn
  down (the app has no self-service password-change endpoint, so there is nothing else to hook):
  - `/me/mfa/disable` in `backend/app/api/mfa.py` (user disables their own MFA).
  - `/users/{user_id}/mfa/reset` in `backend/app/api/mfa.py` (admin resets a user's MFA).
- **Frontend** `frontend/src/pages/LoginPage.tsx` (checkbox), a new trusted-devices section on the
  security page, the admin toggle in System settings, i18n for all 13 locales.

## Data model

`trusted_device`:

| column | type | notes |
|---|---|---|
| `id` | UUID pk | |
| `user_id` | UUID FK users(id) ON DELETE CASCADE, indexed | trust owner |
| `token_hash` | String(64), unique, indexed | `HMAC-SHA256(SESSION_SECRET, raw_token)` |
| `user_agent` | String(512), nullable | display only |
| `ip` | String(45), nullable | display only |
| `created_at` | DateTime, server_default now() | |
| `last_used_at` | DateTime, server_default now() | bumped on each skip |
| `expires_at` | DateTime, indexed | absolute expiry |

Migration `0045_trusted_device.py`, `down_revision = "0044"`.

## Data flow

### Login with a trusted device

1. `POST /api/login` with email+password. Password verifies.
2. If `trusted_device_enabled` is on, the user is MFA-enrolled, and the request carries a valid
   `opngms_trusted_device` cookie whose token_hash matches a non-expired, non-revoked row for **this
   user** → `touch` the row, mint a `full` session, set session+csrf cookies, return
   `status: "ok"` with the user. Audit `auth.login.trusted_device`. *(Second factor skipped.)*
3. Otherwise the normal decision applies: enrolled → `mfa_pending` (return `status: "mfa_required"`
   with `methods` and `remember_device`), policy-required-but-not-enrolled → `mfa_setup`, else
   `full`.

### Marking a device trusted

1. User completes TOTP (`POST /api/login/mfa`) or WebAuthn (`POST /api/login/webauthn/complete`)
   with `remember_device: true` in the body.
2. On success, after the full session is minted, if `trusted_device_enabled` is on: mint a raw
   token, insert a `trusted_device` row (`expires_at = now + trusted_device_days days`, store UA/IP),
   and `response.set_cookie("opngms_trusted_device", raw, httponly=True, secure=True,
   samesite="lax", max_age=trusted_device_days*86400)`. Audit `auth.trusted_device.create`.
3. If `remember_device` is false/absent, or the toggle is off, no cookie/row is created.

### Revocation

- User lists/revokes via `/me/trusted-devices`. Revoking deletes the row(s); the stale cookie then
  fails the `find_valid` lookup at next login. Audit `auth.trusted_device.revoke`.
- Disabling MFA (self-service) or an admin MFA reset calls `revoke_all(user_id)`.
- Admin turning the org toggle off gates the login skip immediately (existing rows become inert
  without deletion; turning it back on re-honors unexpired cookies — acceptable and documented).
- Expired rows are filtered at read time and purged by the existing session sweeper.

## Error handling

- Malformed / unknown / expired / wrong-user trusted cookie → treated as absent; fall through to the
  normal MFA flow (fail-closed: a bad cookie never grants a skip).
- `remember_device` honored only when the toggle is on; otherwise silently ignored (no row/cookie).
- `find_valid` compares the recomputed token_hash and the row's `user_id` against the
  password-authenticated user; a mismatch is treated as no trust.

## Security considerations

- Password is always required; only the second factor is skipped.
- Trust is bound to `user_id`; a cookie for user A can never skip MFA for user B.
- `token_hash` keyed by `SESSION_SECRET` → DB dump useless; secret rotation invalidates all.
- Cookie is HttpOnly + Secure + SameSite=Lax (JS cannot read it; not sent cross-site).
- A trusted cookie never bypasses mandatory enrollment (`mfa_setup` still forced when policy
  requires and the user isn't enrolled).
- Management mutations require a full session + CSRF.
- Audit trail: `auth.trusted_device.create`, `auth.login.trusted_device`,
  `auth.trusted_device.revoke`.
- Adversarial security review after implementation (auth + cookie + cross-user paths).

## Testing

Backend:
- Valid trusted cookie → `/login` returns `status: "ok"` and a `full` session (no MFA round-trip);
  `last_used_at` bumped.
- Expired / wrong-user / unknown / absent cookie → `/login` still returns `mfa_required`.
- `remember_device: true` on `/login/mfa` creates a row + sets the cookie; same for
  `/login/webauthn/complete`.
- `remember_device` ignored (no row/cookie) when the toggle is off.
- Toggle off → `/login` never skips even with a valid cookie; `LoginOut.remember_device.enabled` is
  false.
- `/me/mfa/disable` and the admin MFA reset purge all trusted devices for the user.
- `GET/DELETE /me/trusted-devices` list/revoke (one + all), 404 on another user's id.
- Expired rows filtered from the list.

Frontend:
- Checkbox renders on the MFA step only when `remember_device.enabled`; label shows the day count.
- The flag is passed through to `/login/mfa` and `/login/webauthn/complete`.
- Trusted-devices section lists and revokes.

## PR plan

- **PR1 — backend** (this spec + the plan + all backend): model + migration 0045, service, settings
  (toggle + runtime day count), login-skip, completion-set-cookie, `LoginOut.remember_device`,
  management API, auto-revoke on disable-MFA + admin-MFA-reset, audit, tests. Tag nothing yet.
- **PR2 — frontend**: login checkbox, trusted-devices security section, admin toggle, i18n ×13,
  vitest. Then refresh README + Wiki, tag **v0.21.0**.

Two PRs keep each reviewable; the feature is usable after both.

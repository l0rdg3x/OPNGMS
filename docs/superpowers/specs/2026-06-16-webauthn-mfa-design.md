# WebAuthn / passkey as a second MFA factor

**Date:** 2026-06-16
**Status:** design approved (brainstorm) — ready for implementation plan
**Part of:** MFA-future (sub-project A; sub-project B = remember-this-device, separate).

## Problem

Login MFA today is **TOTP-only** (`pyotp`, one `user_mfa` row per user, + argon2 recovery codes), enforced
by an org-wide policy (`off`/`all`/`privileged`). Add **WebAuthn** (security keys / platform passkeys —
Face/Touch ID, Windows Hello, YubiKey) as a **second factor alongside TOTP**: a user can register one or
more passkeys and satisfy the login MFA challenge with a passkey **or** a TOTP code.

## Goal

WebAuthn as an additional second factor (NOT passwordless): password first, then a passkey **or** TOTP.
A user "has MFA" (for the existing policy) if they have a confirmed TOTP **or** ≥1 passkey. Keep the
existing TOTP + recovery-code paths unchanged.

## Key constraint — RP ID / origin

WebAuthn binds credentials to a **Relying Party ID** (the registrable domain) and verifies the **origin**;
it requires **HTTPS** + a stable domain. OPNGMS is self-hosted and today has no configured public URL, so:
- Add runtime settings (env default, DB override — the existing System → Runtime settings registry):
  `webauthn_rp_id` (e.g. `opngms.example.com`), `webauthn_rp_name` (default `OPNGMS`), `webauthn_origin`
  (e.g. `https://opngms.example.com`).
- While `webauthn_rp_id`/`webauthn_origin` are unset, passkey **registration is disabled** (the API returns
  a clear "WebAuthn not configured" error and the UI hides/greys the "Add passkey" action). Existing TOTP is
  unaffected. This is documented as an operator prerequisite (set the domain once).

## Components

### Dependency
- Add **`webauthn`** (py_webauthn) to `backend/pyproject.toml` — the standard library for the
  attestation/assertion ceremonies (it does the CBOR/COSE + signature verification).

### Data model
- New table **`webauthn_credential`** (N per user): `id` (uuid pk), `user_id` (fk → users, indexed),
  `credential_id` (bytea, **unique**), `public_key` (bytea, COSE), `sign_count` (bigint), `transports`
  (text[] nullable), `name` (text — user label), `aaguid` (text nullable), `created_at`, `last_used_at`
  (nullable). Migration `0044`.
- Session: add a nullable **`webauthn_challenge`** (text) column to `sessions` (migration `0044` too) — the
  per-ceremony challenge, bound to the session (login ceremony uses the `mfa_pending` session; registration
  uses the user's `full` session) and cleared on completion. (Same row already expires, giving the challenge
  a natural TTL.)

### Service — `app/services/webauthn.py` (new)
Thin wrappers over `py_webauthn`, pure where possible:
- `registration_options(user, rp_id, rp_name, origin, existing_cred_ids) -> (options_json, challenge)` —
  `generate_registration_options` (exclude already-registered creds; user-verification "preferred";
  resident key not required). Returns the options dict (for the browser) + the raw challenge to persist.
- `verify_registration(response, challenge, rp_id, origin) -> VerifiedRegistration` — `verify_registration_response`;
  raises a typed `WebAuthnError` on mismatch. Caller stores credential_id/public_key/sign_count/transports.
- `authentication_options(rp_id, allow_cred_ids) -> (options_json, challenge)` — `generate_authentication_options`.
- `verify_authentication(response, challenge, rp_id, origin, public_key, sign_count) -> new_sign_count` —
  `verify_authentication_response`; enforces the **sign-count increase** (anti-cloned-authenticator); raises
  `WebAuthnError` on mismatch.
All inputs/outputs use base64url (py_webauthn helpers). No secret/credential material is logged.

### API — extend `app/api/mfa.py` + `app/api/auth.py`
Registration (authenticated — a `full` session, or an `mfa_setup` session so a policy-forced user can enroll
a passkey instead of TOTP):
- `POST /api/me/mfa/webauthn/register/begin` — 409 if WebAuthn unconfigured; build options, store challenge
  on the session, return options.
- `POST /api/me/mfa/webauthn/register/complete` — verify against the stored challenge, persist a
  `webauthn_credential` (with an optional `name`), clear the challenge, audit `mfa.webauthn.add`. Confirming
  the first passkey also satisfies enrollment (the user now "has MFA").
- `GET /api/me/mfa/webauthn/credentials` — list (id, name, created_at, last_used_at) — no key material.
- `DELETE /api/me/mfa/webauthn/credentials/{id}` — remove one (audit `mfa.webauthn.remove`); cannot remove
  the last MFA factor while the policy requires MFA for the user (mirror the existing TOTP-disable guard).

Login (on an `mfa_pending` session):
- `POST /api/login/webauthn/begin` — options for the user's registered creds, store challenge on the session.
- `POST /api/login/webauthn/complete` — verify the assertion, bump `sign_count` + `last_used_at`, then
  **mint a fresh `full` session** (anti-fixation rotation — exactly like `/login/mfa`), audit `auth.login`.

`GET /api/me/mfa` (status) gains `webauthn: {configured: bool, credentials: int}` so the UI knows whether to
offer passkeys. The login decision in `auth.py` changes `enrolled` from `mfa_row.enabled` to
`mfa_row.enabled OR has_webauthn_credentials(user)` (a small helper); the `LoginOut` for `mfa_required`
indicates which methods are available (`{"methods": ["totp", "webauthn"]}`) so the UI shows the right options.

### Frontend — `frontend/src/...`
- **Two-factor auth page** (`MfaPage`): an "Add passkey" button (only when `webauthn.configured`) →
  `navigator.credentials.create` with the options from `register/begin`, base64url-encode the result, POST to
  `register/complete`; a list of registered passkeys with names + remove buttons.
- **Login MFA step** (the existing `mfa_required` screen): when `methods` includes `webauthn`, show a "Use a
  passkey / security key" button → `navigator.credentials.get` from `login/webauthn/begin`, POST to
  `login/webauthn/complete`; the TOTP code field stays for `totp`. A small base64url + WebAuthn helper module
  (`webauthnClient.ts`) does the `ArrayBuffer`↔base64url plumbing.
- New i18n keys (add-passkey, use-passkey, name-your-passkey, not-configured, remove, …) across all 12 locales.

## Security
- The challenge is generated server-side, **persisted on the session row** (not the client), single-use
  (cleared on completion), and naturally expires with the session. Origin + RP ID are verified by
  py_webauthn against the configured values. The **sign-count must strictly increase** (cloned-authenticator
  detection) — a non-increase fails the assertion. No credential/key material or challenge is logged. The
  remove-last-factor guard prevents a user from locking MFA off while the policy requires it. The new
  endpoints are CSRF-protected on mutation like the rest of `/api/me/mfa` + `/api/login/*`. Run the
  adversarial security review + ensure the CodeQL extended suite is clean.

## Testing
- **Unit** (no browser): the `webauthn.py` wrappers against py_webauthn's own test vectors / a software
  authenticator helper (py_webauthn ships testing utilities) — registration verify success + tampered
  challenge/origin fail; authentication verify success + **sign-count-replay fails**. `has_webauthn` /
  enrolled-OR logic. Credential model round-trip. API: register begin/complete happy path (with a stubbed
  verify), `409` when unconfigured, list hides key material, delete-last-factor guard, login/webauthn mints a
  full session. Login-decision: a user with only a passkey gets `mfa_required` with `methods:["webauthn"]`.
- **Frontend**: a test that the login MFA step shows the passkey button when `methods` includes webauthn, and
  the MfaPage shows "Add passkey" only when configured (mock `navigator.credentials`).
- **Live (operator, needs a browser + a real authenticator)**: set `webauthn_rp_id`/`origin`, register a
  passkey (platform or security key), log out, log in with the passkey. Can't run in CI.
- Gate: `cd backend && python -m pytest -q` + `ruff check app/` + `cd frontend && npm run build && npm test && npm run lint`.

## PR breakdown
- **PR1 — backend** (this spec's core): dependency, model + migration, `webauthn.py` service, the register +
  login + list/delete API, the `auth.py` enrolled-OR-passkey decision, settings, tests.
- **PR2 — frontend**: MfaPage add/list/remove passkey + login passkey button + `webauthnClient.ts` + i18n,
  tests.
(Two PRs keep each reviewable; the feature is usable after both. Sub-project B "remember-this-device" is a
separate later effort.)

## Out of scope
- **Passwordless / first-factor** WebAuthn (this is strictly a second factor after the password).
- **Remember-this-device** (sub-project B).
- Per-tenant MFA policy (the policy stays org-wide).
- Attestation-conveyance / enterprise attestation policies (we accept `none`/`indirect`; we don't pin AAGUIDs).

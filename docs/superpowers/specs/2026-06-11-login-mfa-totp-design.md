# Login MFA (TOTP) — Design Spec

**Status:** Design approved (brainstorming, 2026-06-11). Factor = TOTP + recovery codes; enforcement =
self-enroll + superadmin policy; superadmin break-glass + admin reset of other users.
**Date:** 2026-06-11

## Goal

Add **multi-factor authentication** to the OPNGMS login: a time-based one-time password (TOTP, RFC
6238) second factor with one-time **recovery codes**, layered on the existing email+password →
server-side session flow without weakening the current session/CSRF/SEC-3 model.

## Decisions (locked in brainstorming)

- **Factor:** TOTP (any authenticator app) + one-time **recovery codes**. Architecture stays open to
  WebAuthn as a later factor.
- **Enrollment:** self-service per user (Account → Security), requiring a fresh password re-auth to
  change MFA settings.
- **Enforcement:** a global, superadmin-set policy `mfa_required ∈ {off, all, privileged}`. When the
  policy applies to a user who is not enrolled, login yields a **setup-only session** that can reach
  only the enrollment endpoints until they enroll (fail-closed).
- **Recovery / break-glass (two layers):**
  1. **Recovery codes** — first-line self-recovery (lost phone, still has codes).
  2. **Host-level CLI break-glass** — `python -m app.cli mfa-reset --email <e>` (runs on the
     deployment with `ADMIN_DATABASE_URL`, not exposed on the web) clears a user's MFA. This is the
     reliable path for the **last superadmin** who lost both their TOTP device and recovery codes,
     since no web actor can reset them.
- **Superadmin admin-reset:** a superadmin can reset **another** user's MFA via the API (to unlock
  locked-out staff/customer users).
- **My implementation choices (flagged & approved):** (a) the password-verified-but-MFA-pending state
  is a **server-side pending session** (consistent with SEC-3, revocable) — not a separate token;
  (b) enforcement uses a **setup-only session scope**; (c) **no "remember this device"** in the MVP —
  every login requires the factor.

## Data model

Two new tables + one policy setting. Kept separate from `users` to keep that table clean.

- **`user_mfa`** (1:1 with users):
  - `user_id` UUID PK/FK → users (ON DELETE CASCADE)
  - `enabled` bool (default false) — true only after a confirmed enrollment
  - `totp_secret_enc` bytea — the TOTP secret **encrypted at rest** with `MASTER_KEY` (Fernet), the
    same mechanism as device credentials (`crypto.encrypt/decrypt`)
  - `confirmed_at` timestamptz | null
  - `last_used_step` bigint | null — the last accepted TOTP time-step (anti-replay)
  - `created_at` / `updated_at`
  - RLS: this is user-scoped, not tenant-scoped; it follows the users table's access model (no
    tenant predicate). The API only ever reads/writes the current user's row (or, for admin reset, a
    superadmin-gated path). No `tenant_id`.
- **`user_recovery_code`**:
  - `id` UUID PK, `user_id` UUID FK (ON DELETE CASCADE)
  - `code_hash` text — argon2 hash of a one-time code (never stored in clear)
  - `used_at` timestamptz | null
  - `created_at`
- **MFA policy:** there is **no** existing global settings store (only `report_settings`, which is
  tenant-scoped). Introduce a minimal global key/value table **`app_settings`** (`key` text PK,
  `value` jsonb, `updated_at`) with a tiny get/set helper. The policy lives at key `mfa_required`
  with value `"off" | "all" | "privileged"` (default `off` when the key is absent). `app_settings`
  is **not** tenant-scoped (no RLS predicate); only superadmin-gated endpoints write it.

A new Alembic migration creates these. The ORM `Base.metadata` gains the models (so the test/conftest
`create_all` path and the screenshot DB recipe both pick them up).

## Crypto & helpers

- **TOTP:** `pyotp` (new backend dependency) — `pyotp.random_base32()` to mint a secret,
  `pyotp.TOTP(secret).verify(code, valid_window=1)` for ±1 step (30 s) skew tolerance, and
  `provisioning_uri(name=email, issuer_name="OPNGMS")` for the `otpauth://` URI.
- **QR:** the backend returns the `otpauth://` URI + the base32 secret; the **frontend renders the QR**
  (a small QR component) — no server-side image dependency.
- **Recovery codes:** generate 10 codes (e.g. `xxxxx-xxxxx`, base32/crockford, ~50 bits each), hash
  each with argon2 (`security.hash_password` reused or a dedicated hasher), store the hashes, return
  the clear codes to the client **once**.
- **Anti-replay:** on a successful TOTP verify, persist the accepted time-step in `last_used_step`
  and reject any code whose step ≤ `last_used_step`.

## Enrollment flow (authenticated; requires fresh password re-auth)

All under `/api/me/mfa/*`, `get_current_user` + `enforce_csrf`. A "fresh password re-auth" means the
request body carries the current password, re-verified server-side, for the mutating calls
(`setup`, `disable`, `recovery/regenerate`).

1. `POST /api/me/mfa/setup { password }` → verify password; mint a secret; upsert `user_mfa`
   (`enabled=false`, `totp_secret_enc`, `confirmed_at=null`); return `{ otpauth_uri, secret }`.
   Re-calling `setup` before confirming replaces the unconfirmed secret.
2. `POST /api/me/mfa/confirm { code }` → verify `code` against the pending secret; on success set
   `enabled=true`, `confirmed_at=now`, set `last_used_step`; (re)generate the 10 recovery codes and
   return them **once** as `{ recovery_codes: [...] }`. On failure → 422, not enabled.
3. `POST /api/me/mfa/disable { password }` → verify password; delete the `user_mfa` row + recovery
   codes (`enabled→false`). If policy requires MFA for this user, the next request falls back to the
   setup-only session (they must re-enroll).
4. `POST /api/me/mfa/recovery/regenerate { password }` → verify password + that MFA is enabled;
   replace all recovery codes; return the new set once.
5. `GET /api/me/mfa` → `{ enabled, confirmed_at, recovery_codes_remaining }` (never the secret).

## Login flow (two-step)

1. `POST /api/login { email, password }` (existing rate-limit + audit):
   - password invalid → unchanged (401, rate-limited).
   - password valid **and** user has `user_mfa.enabled` → do **not** issue a full session. Create a
     **pending session** (`Session` row with `kind="mfa_pending"`, ~5-min TTL, no privileges) and set
     a short-lived pending cookie; respond `200 { mfa_required: true }`.
   - password valid **and** no MFA → issue a full session exactly as today, **unless** the policy
     requires MFA for this user (see Enforcement) → issue a **setup-only** session and respond
     `{ mfa_setup_required: true }`.
2. `POST /api/login/mfa { code }` (carries the pending cookie):
   - resolve the pending session (reject if absent/expired); verify `code` as a **TOTP** (anti-replay)
     **or** a **recovery code** (mark it `used_at`); on success **upgrade**: delete the pending
     session, issue the full session cookie + CSRF (the existing `AuthService` session creation),
     audit `mfa.login_success` (note recovery-code use). On failure → 401, **rate-limited** on the
     same `email|ip` key as login; the pending session survives until its TTL.

The pending session has no access to any app endpoint — only `/api/login/mfa` and `/api/logout`
accept it.

## Enforcement (policy → setup-only session)

- The policy `mfa_required` applies to a user when: `all`, or (`privileged` and the user is
  `is_superadmin` or holds a `tenant_admin` membership).
- At login, if the policy applies and the user is **not** enrolled, issue a **setup-only session**
  (`Session.kind="mfa_setup"`): the auth dependency permits ONLY `/api/me/mfa/*` and `/api/logout`;
  every other protected route returns `403 { mfa_setup_required: true }` until the user confirms
  enrollment, at which point the session is upgraded to full (or they re-login).
- Superadmin sets the policy: `PUT /api/admin/mfa-policy { mode }` (superadmin-only, CSRF, audit
  `policy.change`); `GET /api/admin/mfa-policy`.

## Superadmin admin-reset & break-glass

- **Admin reset (web):** `POST /api/users/{user_id}/mfa/reset` — **superadmin-only**, CSRF, audit
  `mfa.admin_reset`. Clears the target's `user_mfa` + recovery codes. The user is then unenrolled
  (and, if policy requires, hits the setup-only gate on next login). A superadmin may also reset
  another superadmin; but the genuine "last superadmin locked out, no one left to reset them" case is
  covered by the host-level CLI break-glass below.
- **Break-glass CLI:** `python -m app.cli mfa-reset --email <email>` (new `app/cli.py` Typer/argparse
  entrypoint) connects via `ADMIN_DATABASE_URL`, clears the user's MFA, and prints a confirmation +
  audit row. Runs only on the host (deployment shell) — the reliable recovery for the **last
  superadmin** locked out of the web. Documented in the README ops section.
- Recovery codes remain the first-line self-recovery for any enrolled user.

## Security details

- TOTP secret **encrypted at rest** (`MASTER_KEY`); never returned after enrollment.
- Recovery codes **argon2-hashed**, one-time (`used_at`), shown once.
- MFA-setting mutations require a **fresh password** re-auth.
- TOTP verify: `valid_window=1`; **anti-replay** via `last_used_step`.
- MFA verification step is **rate-limited** (reuse `SlidingWindowLimiter`, same `email|ip` key,
  fail-closed) to stop code brute-force.
- Pending / setup-only sessions are **distinct kinds** with no app access; both honour SEC-3 lifecycle
  (TTL, revocation).
- Full **audit**: `mfa.enroll`, `mfa.confirm`, `mfa.disable`, `mfa.recovery_regenerate`,
  `mfa.login_success` (+recovery flag), `mfa.login_failed`, `mfa.recovery_used`, `mfa.admin_reset`,
  `mfa.policy_change`.
- No user-enumeration regression: `login` responses for "MFA required" vs "full session" differ, but
  only **after** a correct password — so they leak nothing to an unauthenticated attacker.

## Frontend

- **Login** (`LoginPage`): after the password step, if `{ mfa_required }` render a 6-digit TOTP input
  (+ "use a recovery code" toggle) posting to `/api/login/mfa`; if `{ mfa_setup_required }` route to
  the forced enrollment gate.
- **Account → Security** (new page/section + nav under the existing `/security/*`): MFA status, an
  **Enroll** wizard (password → show QR (rendered from `otpauth_uri`) + secret → confirm code → show
  recovery codes once with a copy/download), **Regenerate recovery codes**, **Disable** (password).
- **Forced enrollment gate**: when the session is setup-only, a full-screen gate routes to enrollment
  and blocks the rest of the app.
- **Superadmin**: an MFA-policy control (off / all / privileged) in an admin/settings area; and a
  **Reset MFA** action on a user in the users list.
- A small QR component (e.g. a dependency-free SVG QR or a tiny lib); i18n under `auth.mfa.*`.
- Regenerate API types after the endpoints land.

## Testing

- **Backend unit:** TOTP verify (valid/invalid/skew/replay-rejected), recovery code one-time use,
  secret encryption round-trip, recovery-code hashing, policy-applies logic, pending→full upgrade,
  setup-only gating.
- **Backend API:** enroll/confirm/disable/regenerate (+ password re-auth required), two-step login
  (password→mfa, recovery-code path), rate-limit on the MFA step, enforcement gate (setup-only),
  superadmin policy get/set, admin reset (superadmin-only; cross-actor authz), audit rows.
- **CLI:** `mfa-reset` clears a user's MFA (against the test DB).
- **Frontend:** login MFA step (TOTP + recovery toggle), enrollment wizard, forced gate, policy
  control, admin reset.
- **Live/manual:** enroll with a real authenticator app against the running stack; confirm two-step
  login, recovery-code login, admin reset, and the CLI break-glass.

## Out of scope (follow-ups)

- WebAuthn/passkeys; "remember this device"; per-tenant MFA policy (only global `mfa_required` here);
  SMS/email OTP; MFA for the API-token/service paths.

# SEC-3 — Session Lifecycle Hardening (Design)

**Date:** 2026-06-10
**Status:** Approved scope (core + all three optional extensions), pending spec review.

## Goal

Harden the session and CSRF subsystem so that: session tokens are unguessable and
not usable from a database dump, sessions expire on inactivity (not just on an
absolute clock), users can terminate every session at once, CSRF protection
validates a real per-session token (not just header presence), and expired rows
are reaped automatically.

## Background — current state (as built)

- **Session model** (`backend/app/models/session.py`): `sessions(id UUID PK, user_id FK→users CASCADE, created_at, expires_at)`. Indexes on `user_id` and `expires_at`.
- **Cookie** = `str(session.id)` (the UUID **is** the bearer token, stored in plaintext in the DB as the PK). Cookie `opngms_session`: httponly, secure, samesite=lax, max-age = `session_ttl_hours*3600`.
- **Validation** (`core/deps.py:get_current_user` → `services/auth.py:get_user_for_session`): parse UUID from cookie, `SELECT … WHERE id=:id`, reject if missing or `expires_at <= now`. No sliding expiry, no `last_seen`.
- **Logout**: deletes the one current session. No "logout all".
- **CSRF** (`core/deps.py:enforce_csrf`): on POST/PUT/PATCH/DELETE, only checks the **presence** of header `X-OPNGMS-CSRF` (any value passes). Frontend (`frontend/src/api/client.ts`, `reportHooks.ts`, `settingsHooks.ts`) sends the hardcoded value `"1"`.
- **No cleanup cron** for expired sessions. ARQ cron pattern lives in `backend/app/worker.py` (`cron_jobs` + `functions` lists).
- **Migrations**: head is `0013`; numeric string revisions, `down_revision` chained.

## Scope

Core (roadmap): per-session CSRF token, logout-all, session rotation, expired-session cleanup cron.
Optional (all selected by the user): (A) hash session token at rest, (B) idle/sliding timeout, (C) active-sessions list endpoint.

Out of scope: MASTER_KEY rotation, pagination, any change to RBAC, any hardware-dependent work.

## Design

### 1. Session model changes

Add to `sessions`:

| Column | Type | Notes |
|---|---|---|
| `token_hash` | `String(64)` | **unique**, indexed. `sha256(raw_token)` hex. The lookup key. |
| `csrf_token` | `String(64)` | per-session CSRF secret (urlsafe). |
| `last_seen_at` | `DateTime(timezone=True)` | updated on activity (throttled). |
| `ip` | `String(45)` | client IP captured at login (display-only). IPv6-safe length. |
| `user_agent` | `String(512)` | truncated UA captured at login (display-only). |

`id` (UUID PK) is **kept** but is no longer the bearer token — it becomes a safe public handle for the active-sessions list. Existing indexes on `user_id`/`expires_at` are kept; add a **unique** index on `token_hash`.

**Token formats:**
- Bearer token (cookie value): `secrets.token_urlsafe(32)` (~43 chars). Stored only as `token_hash = hashlib.sha256(token.encode()).hexdigest()`.
- CSRF token: `secrets.token_urlsafe(32)`.

**Migration 0014** (`backend/migrations/versions/0014_session_hardening.py`): `add_column` the five columns (all nullable — no backfill), create the unique index on `token_hash`. `downgrade()` drops them. After deploy, pre-existing cookies (raw UUIDs) no longer match any `token_hash` → users silently re-login. This is acceptable and intended for a credential-format change.

### 2. Config additions (`backend/app/core/config.py`)

- `session_idle_minutes: int = 120` — inactivity timeout.
- Keep `session_ttl_hours` (default 12) as the **absolute** cap.
- New cookie name constant `CSRF_COOKIE = "opngms_csrf"` (place next to `SESSION_COOKIE` in `core/deps.py`).

### 3. Auth service (`backend/app/services/auth.py`)

Replace UUID-centric methods with token-centric ones:

- `create_session(user, *, ttl_hours, ip, user_agent) -> tuple[Session, str]`
  Generates `raw_token` + `csrf_token`; stores `token_hash`, `csrf_token`, `expires_at = now + ttl`, `last_seen_at = now`, `ip`, `user_agent`; sets `user.last_login`. Returns `(session, raw_token)` so the API can set the cookie with the raw token (never persisted).
- `get_session_for_token(raw_token) -> Session | None`
  `sha256` the token, `SELECT … WHERE token_hash=:h`. Reject if missing, if `expires_at <= now` (absolute), or if `now - last_seen_at > idle_ttl` (idle). On success, **throttled** `last_seen_at` update: only write if `now - last_seen_at >= 60s` (avoids a write per request).
- `get_user_for_session(session) -> User`
- `delete_session_by_token(raw_token)` and `delete_all_sessions_for_user(user_id)`
- `list_sessions_for_user(user_id) -> list[Session]` (ordered by `last_seen_at` desc)
- `purge_expired(now) -> int` — `DELETE WHERE expires_at <= now OR last_seen_at < now - idle_ttl`; returns rowcount (for the cron + log).

### 4. Dependencies (`backend/app/core/deps.py`)

Introduce one shared dependency and rebuild the others on top of it:

- `get_current_session(request, db) -> Session`
  Reads `SESSION_COOKIE`, calls `get_session_for_token`, raises 401 if absent/invalid/expired. Stashes the session on `request.state.session`.
- `get_current_user(session=Depends(get_current_session), db=…) -> User` — returns the user for the session.
- `enforce_csrf(request, session=Depends(get_current_session)) -> None`
  For POST/PUT/PATCH/DELETE: require `X-OPNGMS-CSRF` header and `secrets.compare_digest(header, session.csrf_token)`. Missing/mismatch → 403. GET/HEAD/OPTIONS pass. FastAPI dedupes `get_current_session` when an endpoint depends on both `get_current_user` and `enforce_csrf`, so it runs once per request.

`/setup` and `/login` keep **no** CSRF dependency (no session yet). All other mutating endpoints already declare `Depends(enforce_csrf)` — unchanged.

### 5. API (`backend/app/api/auth.py`)

- **`POST /login`**: capture `ip` (first hop of `X-Forwarded-For` if present, else `request.client.host`) and `user_agent` (truncated 512). Create session; set `SESSION_COOKIE = raw_token` (httponly, secure, samesite=lax, max-age=ttl) **and** `CSRF_COOKIE = csrf_token` (secure, samesite=lax, max-age=ttl, **not** httponly so the SPA can read it). **Rotation:** if the incoming request already carries a valid session cookie, delete that session first.
- **`POST /logout`**: delete current session by token; clear both cookies.
- **`POST /logout-all`** (NEW; `Depends(get_current_user)` + `Depends(enforce_csrf)`): `delete_all_sessions_for_user(user.id)`; clear both cookies; 204.
- **`GET /sessions`** (NEW; `Depends(get_current_user)`): returns `[{id, created_at, last_seen_at, expires_at, ip, user_agent, current}]`, where `current = (row.id == request.state.session.id)`. Pydantic response model `SessionInfo`.

IP/UA are **display-only** (no security decision keys off them); trust assumption is that the API is reachable only via the trusted nginx ingress. nginx already proxies `/api` to the API on the same origin.

### 6. Cleanup cron (`backend/app/worker.py`)

```python
async def cleanup_expired_sessions(ctx: dict) -> str:
    factory = ctx["session_factory"]
    async with factory() as session:
        n = await AuthService(session).purge_expired(datetime.now(timezone.utc))
        await session.commit()
    return f"purged {n} expired sessions"
```

Register: `cron(cleanup_expired_sessions, minute={0})` (hourly) in `cron_jobs`, and add the function to `functions`.

### 7. Frontend

- **CSRF token delivery (mandatory — the app breaks otherwise):** add `frontend/src/api/csrf.ts` exporting `csrfToken()` that reads the `opngms_csrf` cookie. Replace the hardcoded `"1"` in `client.ts` (middleware), `reports/reportHooks.ts`, and `reports/settingsHooks.ts` with `csrfToken()`.
- **Active sessions UI (additive):** a "Security / Sessions" view (route + nav entry) listing active sessions from `GET /api/sessions` (current one badged), plus a **"Log out everywhere"** button calling `POST /api/logout-all` then redirecting to login. Uses existing Mantine + TanStack Query patterns. New `frontend/src/security/` module + hooks. i18n strings added to the existing locale files.

### 8. Tests

**Backend** (`backend/tests/`):
- `test_csrf.py` (rewrite): mutating request with **no** header → 403; with **wrong** token → 403; with the **session's** csrf cookie value echoed in the header → success. Add a helper to read the `opngms_csrf` cookie from the client jar.
- `test_sessions.py` (new): token is hashed at rest (`sessions.token_hash != cookie value`, no plaintext token column); tampered cookie → 401; idle timeout (manually backdate `last_seen_at` → 401); absolute expiry still enforced; rotation (old session row gone after a second login); `logout-all` deletes all rows for the user and 401s afterwards; `GET /sessions` lists sessions and flags `current`.
- `test_worker.py` (or existing worker tests): `purge_expired` deletes expired + idle rows, keeps live ones.

**Frontend**: update tests/MSW that assumed `"1"`; add a test that mutations send the cookie's token; a render test for the sessions view + logout-all.

## Migrations & ordering

`0014_session_hardening` is the only new migration. No data backfill. Worker (owner role) runs migrations; the app role keeps RLS — no policy change (sessions has no tenant_id; it is keyed by user_id and untouched by RLS).

## Risks / decisions

- **Forced re-login on deploy** — intended (token format changes). Documented in the release note.
- **Throttled `last_seen` writes** keep the per-request write cost negligible while still enforcing idle timeout within ~60s granularity.
- **IP via XFF** is display-only; not a security control, so spoofing is low-impact and the nginx trust boundary covers the realistic case.
- **CSRF coupling to a valid session** is correct: unauthenticated endpoints (`/login`, `/setup`) are intentionally CSRF-exempt; every authenticated mutation already requires both auth and CSRF.

## File map

- Modify: `backend/app/models/session.py`, `backend/app/core/config.py`, `backend/app/core/deps.py`, `backend/app/services/auth.py`, `backend/app/api/auth.py`, `backend/app/worker.py`
- Create: `backend/migrations/versions/0014_session_hardening.py`, `backend/tests/test_sessions.py`
- Modify tests: `backend/tests/test_csrf.py`, `backend/tests/test_auth.py` (login now sets two cookies)
- Frontend modify: `frontend/src/api/client.ts`, `frontend/src/reports/reportHooks.ts`, `frontend/src/reports/settingsHooks.ts`
- Frontend create: `frontend/src/api/csrf.ts`, `frontend/src/security/` (view + hooks), nav/route wiring, i18n strings
- Docs: update `README.md` Roadmap & status (SEC-3 row).

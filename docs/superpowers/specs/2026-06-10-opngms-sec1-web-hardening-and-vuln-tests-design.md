# OPNGMS — Security Milestone SEC-1: Web Hardening + Vulnerability Test Suite — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user asked for an urgency-ordered hardening roadmap, to start fixing in order, and a general vulnerability test)
- **Milestone:** SEC-1 — the first security-hardening milestone (P0 web/transport items + a consolidated vulnerability test suite + dependency audit + CI)
- **Depends on:** the whole app in `main`
- **Enables:** a production-safer baseline; recurring vulnerability scanning

## 1. Context

A dependency scan baseline is clean (`npm audit` 0; `pip-audit` 0 on app deps — only `pip` itself flagged).
The real exposure is the **application surface**: no security headers, no CORS policy, no login
rate-limiting, only successful logins are audited, and the app-role DB password is hardcoded. SEC-1
closes the cleanly-implementable P0 items and adds a **consolidated vulnerability test suite** + a
dependency-audit + a **CI** pipeline so regressions and new CVEs are caught.

## 2. Scope (SEC-1)

| # | Item | Decision |
|---|------|----------|
| 1 | **Security headers** | A middleware adds `Strict-Transport-Security`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy` (deny camera/mic/geo), and a conservative `Content-Security-Policy` (`default-src 'self'`; the SPA is same-origin). Applied to all responses. |
| 2 | **CORS** | Explicitly **closed by default** (same-origin via the nginx proxy). A `cors_allow_origins` env (comma list, default empty) enables `CORSMiddleware` only when set — no wildcard. |
| 3 | **Login rate-limiting + failed-login audit** | A sliding-window limiter keyed on `(email, client_ip)`: after `LOGIN_MAX_ATTEMPTS` (default 5) failures within `LOGIN_LOCKOUT_WINDOW_SECONDS` (default 900) → `429` (with `Retry-After`). Success resets the counter. **Failed logins are now audited** (`auth.login.failed`). In-memory (per-process) for now — Redis-backed is the multi-worker upgrade (debt). |
| 4 | **App-role password from env** | `Settings.app_role_password` (env `APP_ROLE_PASSWORD`, default `"opngms_app"` for dev); `db_roles` sources it; migration `0003` uses it; `.env.example` documents it + the matching `DATABASE_URL`. Also bump `pip` in the backend image. |
| 5 | **Vulnerability test suite** | `tests/test_security_suite.py`: holistic assertions that the guards hold — CSRF enforced on a mutation, RLS cross-tenant isolation, SSRF guard blocks cloud-metadata/loopback, config **secret redaction**, **security headers** present, **rate-limit** triggers, **SQL-injection allowlist** rejects a hostile `field`, **XXE** is neutralised (defusedxml). Reuses existing fixtures. |
| 6 | **Dependency audit + CI** | A `scripts/security_audit.sh` (runs `pip-audit` + `npm audit`) and a `.github/workflows/ci.yml` (backend: Timescale service + migrate + pytest + ruff; frontend: npm ci + test + build + lint; a dependency-audit job). |

## 3. Components

- `app/core/security.py` (new): `SecurityHeadersMiddleware` (+ a helper to read CSP/headers config). `app/main.py` adds it + the optional `CORSMiddleware`.
- `app/core/config.py`: `app_role_password`, `cors_allow_origins`, `login_max_attempts`, `login_lockout_window_seconds`.
- `app/core/ratelimit.py` (new): a small in-process sliding-window limiter (`hit(key) -> (allowed, retry_after)`, `reset(key)`), time injected for testability.
- `app/api/auth.py`: enforce the limiter before authenticating; audit failed logins; 429 on lockout.
- `app/core/db_roles.py`: `APP_ROLE_PASSWORD` from settings.
- `migrations/versions/0003_*`: use the settings password (read at migration time).
- `tests/test_security_suite.py` (new) + `tests/test_security_headers.py` + `tests/test_login_ratelimit.py`.
- `scripts/security_audit.sh`, `.github/workflows/ci.yml`.

## 4. Security & safety

- Headers are **add-only** (no behavior change); CSP is `default-src 'self'` (the SPA has no inline-script needs beyond Vite's hashed bundles served same-origin — verify the report PDF path is server-side, unaffected). CORS stays closed unless explicitly configured (no wildcard, credentials only with explicit origins).
- The rate-limiter must **fail-open on internal error** (never lock out everyone if the limiter throws) but **fail-closed on lockout** (return 429). Keyed on email+IP so one attacker can't lock another user globally (per-IP scoping limits that; documented).
- App-role password default stays `opngms_app` for dev parity; prod overrides via env (the migration + `DATABASE_URL` must agree — documented).
- No secret is logged; failed-login audit records the email attempted + IP, never the password.

## 5. Milestone SEC-1 breakdown (for the plan)
1. **Security headers + CORS** middleware + config + tests.
2. **Login rate-limiting + failed-login audit** (limiter + config + auth wiring) + tests.
3. **App-role password from env** + `.env.example` + Dockerfile pip bump + tests.
4. **Vulnerability test suite** (`test_security_suite.py`) consolidating the invariants + the new headers/rate-limit checks.
5. **Dependency audit script + CI workflow**.

## 6. Definition of "Done" (SEC-1)
- All responses carry the security headers; CORS is closed by default; login locks out after N failures
  (429) and audits failures; the app-role password is env-configurable; a consolidated vulnerability test
  suite passes; a CI workflow runs tests + lint + dependency audit. Backend + frontend suites green.

## 7. Non-goals (SEC-1) / next milestones
- **TLS fingerprint pinning** on the connector (SEC-2).
- **Session lifecycle** (expired cleanup cron, logout-all, rotation) + **per-session CSRF token** (SEC-3).
- **MASTER_KEY rotation / key-versioning** (later).
- A Redis-backed (multi-worker) rate limiter (in-memory now; recorded as debt).

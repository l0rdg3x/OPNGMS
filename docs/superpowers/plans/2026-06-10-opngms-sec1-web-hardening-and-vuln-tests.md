# OPNGMS — SEC-1: Web Hardening + Vulnerability Test Suite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Close the cleanly-implementable P0 web/transport security gaps (security headers + CORS, login rate-limiting + failed-login audit, app-role password from env), add a **consolidated vulnerability test suite**, and wire a **dependency audit + CI** so regressions/CVEs are caught.

**Spec:** `docs/superpowers/specs/2026-06-10-opngms-sec1-web-hardening-and-vuln-tests-design.md`.

**Tech Stack:** FastAPI/Starlette middleware, pydantic-settings; pytest; GitHub Actions.

---

## Context for the implementer (read first)

- `app/main.py` builds `app = FastAPI(...)` then `include_router(...)` + an IntegrityError handler + `/healthz`. No middleware/CORS today.
- `app/core/config.py` `Settings(BaseSettings)` (env via `.env`): has `database_url`, `session_secret`, `master_key`, `session_ttl_hours`, `admin_database_url`, `redis_url`, `poll_interval_seconds`. `get_settings()` is `@lru_cache`.
- `app/api/auth.py` `POST /api/login` → `AuthService(session).authenticate(email, password)` → 401 on None; else create session + audit `auth.login` + set cookie. No rate-limit; **failed logins are not audited**.
- `app/core/db_roles.py`: `APP_ROLE = "opngms_app"`, `APP_ROLE_PASSWORD = "opngms_app"` (used in `create_app_role_statements()` and by tests/migration 0003).
- `app/services/audit.py` `AuditService(session).record(actor_user_id=, tenant_id=, action=, target_type=, target_id=, ip=, details=)`.
- Tests: `tests/conftest.py` (`api_client`, `app_role_api_client`, `db_engine`), `tests/test_csrf.py`, `tests/test_url_safety.py`, `tests/test_rls_isolation.py`, `tests/test_events_api.py` (helpers). Frontend nginx at `frontend/nginx.conf`.

**Commands** (backend): `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`.

---

## Task 1: Security headers middleware + CORS

**Files:** Create `app/core/security.py`; Modify `app/main.py`, `app/core/config.py`, `frontend/nginx.conf`; Test `tests/test_security_headers.py`.

- [ ] **Step 1: Config** — in `app/core/config.py` add: `cors_allow_origins: str = ""  # comma-separated; empty = CORS disabled (same-origin)`.
- [ ] **Step 2: Middleware** — create `app/core/security.py`:
```python
"""Security response headers (add-only) and a helper for the optional CORS config."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    ),
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        return response
```
- [ ] **Step 3: Wire into `app/main.py`** — after `app = FastAPI(...)`:
```python
from app.core.security import SecurityHeadersMiddleware
from app.core.config import get_settings
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(SecurityHeadersMiddleware)
_origins = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )
```
(No CORS middleware is added when `cors_allow_origins` is empty → same-origin only, no wildcard.)
- [ ] **Step 4: nginx (SPA) headers** — in `frontend/nginx.conf` `server {}`, add `add_header` for the same set (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, a CSP suited to the SPA: `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; object-src 'none'; frame-ancestors 'none'`, and HSTS). Use `add_header ... always;`.
- [ ] **Step 5: Test** — `tests/test_security_headers.py`: a `GET /healthz` (and a JSON endpoint) response carries `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a `Content-Security-Policy`, and HSTS. Also: with `cors_allow_origins` empty, a cross-origin preflight is NOT allowed (no `access-control-allow-origin`). Run + commit `feat(security): security response headers + opt-in CORS (closed by default)`.

---

## Task 2: Login rate-limiting + failed-login audit

**Files:** Create `app/core/ratelimit.py`; Modify `app/core/config.py`, `app/api/auth.py`; Test `tests/test_login_ratelimit.py`.

- [ ] **Step 1: Config** — add `login_max_attempts: int = 5` and `login_lockout_window_seconds: int = 900` to `Settings`.
- [ ] **Step 2: Limiter** — create `app/core/ratelimit.py`:
```python
"""In-process sliding-window limiter (per worker). Redis-backed is the multi-worker upgrade (debt)."""
import time
from collections import defaultdict, deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str, *, now: float | None = None) -> tuple[bool, int]:
        """(allowed, retry_after_seconds). Does not record; call record_failure on a failed attempt."""
        now = time.monotonic() if now is None else now
        with self._lock:
            dq = self._hits[key]
            while dq and dq[0] <= now - self.window:
                dq.popleft()
            if len(dq) >= self.max:
                return False, max(int(self.window - (now - dq[0])) + 1, 1)
            return True, 0

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            self._hits[key].append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)
```
- [ ] **Step 3: Wire into `app/api/auth.py`** — create a module-level limiter built from settings; in `login`:
```python
from app.core.ratelimit import SlidingWindowLimiter
_s = get_settings()
login_limiter = SlidingWindowLimiter(_s.login_max_attempts, _s.login_lockout_window_seconds)
```
In the `login` handler, before authenticating:
```python
    ip = request.client.host if request.client else "?"
    key = f"{payload.email.lower()}|{ip}"
    allowed, retry = login_limiter.check(key)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many attempts", headers={"Retry-After": str(retry)})
```
On auth failure (user is None), BEFORE raising 401:
```python
        login_limiter.record_failure(key)
        await AuditService(session).record(
            actor_user_id=None, tenant_id=None, action="auth.login.failed",
            target_type="auth", target_id=None,
            ip=ip, details={"email": payload.email},
        )
        await session.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
```
On success, `login_limiter.reset(key)` (after creating the session). (Keep the existing success-audit + cookie.)
- [ ] **Step 4: Test** — `tests/test_login_ratelimit.py`: reset `login_limiter` in the test (import it); 5 wrong-password POSTs return 401, the 6th returns 429 with `Retry-After`; a correct login resets so a subsequent wrong attempt is 401 again (not 429); a failed login writes an `auth.login.failed` audit row. (Use `api_client` + `_login_superadmin`-style setup; for the limiter, `login_limiter.reset(key)` at the start to avoid cross-test pollution, or construct keys per-test by unique email.) Run + commit `feat(security): login rate-limiting (lockout after N) + failed-login audit`.

---

## Task 3: App-role password from env + Dockerfile pip bump

**Files:** Modify `app/core/db_roles.py`, `.env.example`, `backend/Dockerfile`; Test (existing pass).

- [ ] **Step 1: Env-sourced password** — in `app/core/db_roles.py` change `APP_ROLE_PASSWORD = "opngms_app"` to `APP_ROLE_PASSWORD = os.getenv("APP_ROLE_PASSWORD", "opngms_app")` (add `import os`). This keeps the dev default, works at import time (no Settings dependency), and lets prod set a strong password. (The CREATE/ALTER ROLE statement interpolates it — the value is operator-controlled/trusted; document avoiding single quotes.)
- [ ] **Step 2: `.env.example`** — add `APP_ROLE_PASSWORD=opngms_app` with a comment: set a strong value in prod and ensure `DATABASE_URL`'s password matches (the migrate job creates/ALTERs the role with this).
- [ ] **Step 3: Dockerfile** — in `backend/Dockerfile`, after the base `FROM`, `RUN pip install --no-cache-dir --upgrade pip` (closes the only pip-audit finding) before `pip install .`.
- [ ] **Step 4: Tests** — the existing suite must stay green (default password unchanged). Add a tiny test asserting `db_roles.APP_ROLE_PASSWORD` honours the `APP_ROLE_PASSWORD` env (monkeypatch + reimport, or assert the default). Run + commit `feat(security): app-role DB password from env (APP_ROLE_PASSWORD) + pip bump in image`.

---

## Task 4: Consolidated vulnerability test suite

**Files:** Create `tests/test_security_suite.py`.

- [ ] **Step 1:** A single suite asserting the security invariants holistically (reuse existing fixtures/helpers):
  - **CSRF**: a mutation (e.g. `POST /api/tenants/{id}/reports` or a device create) without the `X-OPNGMS-CSRF` header → 403. (Mirror `tests/test_csrf.py`.)
  - **RLS cross-tenant**: under `app_role_api_client`, tenant B cannot read tenant A's devices/events (reuse the pattern in `tests/test_devices_rls_api.py`/`test_events_rls_api.py`).
  - **SSRF**: `OpnsenseClient.validate_base_url` (or the connector) rejects `http://169.254.169.254`, loopback, link-local, and non-https (reuse `tests/test_url_safety.py` assertions; import the validator).
  - **Secret redaction**: the config model redacts a `<password>`/`<privkey>` leaf (reuse `tests/test_config_model.py` helper or build a tiny XML) — no secret value in the model output.
  - **Security headers**: a response carries the headers (import from Task 1).
  - **Rate-limit**: N+1 failed logins → 429 (import `login_limiter`, reset first).
  - **SQL-injection allowlist**: `EventRepository.top(field="tenant_id; DROP TABLE events")` raises ValueError (and the `/events/top` API returns 400) — the `field` allowlist holds.
  - **XXE**: parsing a billion-laughs / external-entity XML via the config parser (defusedxml) raises/neutralises (reuse the config backup defensive path).
  Each as a focused test. Mark the suite with a module docstring "OPNGMS application-security regression suite".
- [ ] **Step 2:** Run the whole suite green; commit `test(security): consolidated application-security/vulnerability suite`.

---

## Task 5: Dependency audit script + CI workflow

**Files:** Create `scripts/security_audit.sh`, `.github/workflows/ci.yml`.

- [ ] **Step 1: Audit script** — `scripts/security_audit.sh` (executable): runs `pip-audit` in `backend/` (ignoring the `pip` self-finding via `--ignore-vuln PYSEC-2026-196` or `|| true` with a printed summary) and `npm audit --omit=dev` in `frontend/`; exits non-zero on app-dependency findings. Keep it simple + documented.
- [ ] **Step 2: CI** — `.github/workflows/ci.yml` with jobs:
  - **backend**: `services: timescaledb` (`timescale/timescaledb:2.17.2-pg16`, health-check), checkout, setup-python 3.14, `pip install -e backend[dev]` + `pip-audit`, set `TEST_DATABASE_URL`/`ADMIN_DATABASE_URL`/`SESSION_SECRET`/`MASTER_KEY`, run `pytest -q`.
  - **frontend**: setup-node 20, `npm ci --legacy-peer-deps` in `frontend/`, `npm test`, `npm run build`, `npm run lint`.
  - **audit**: run `scripts/security_audit.sh`.
  (The workflow can't be executed here; ensure valid YAML + that the commands mirror the working local ones. `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` to validate syntax.)
- [ ] **Step 3:** Run `scripts/security_audit.sh` locally (must pass — app deps are clean). Commit `ci: dependency audit script + GitHub Actions (tests, lint, build, audit)`.

---

## Definition of "Done" (SEC-1)
- Responses carry the security headers; CORS closed by default; login locks out after N failures (429) and
  audits failures; the app-role password is env-configurable; the consolidated vulnerability suite passes;
  a CI workflow + audit script exist and the local audit is clean. Backend + frontend suites green.

## Technical debt (SEC-1)
- The rate limiter is **in-process** (per worker) — a Redis-backed limiter is the multi-worker-correct
  upgrade. CSP allows `'unsafe-inline'` styles (Mantine) — tighten with nonces/hashes later. TLS pinning
  (SEC-2), session lifecycle + per-session CSRF token (SEC-3), and MASTER_KEY rotation remain.

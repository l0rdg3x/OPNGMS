# Configurable Tunables Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make deployment tunables configurable — four boot-time values from `.env` and ten
runtime-safe values from the superadmin System page (env/code default + DB override).

**Architecture:** Part A adds four env-backed `Settings` fields wired into the worker, the DB engine,
and the OPNsense client. Part B adds a small generic `RUNTIME_SETTINGS` registry over the existing
`app_setting` key/value store, one `GET/PUT /api/admin/settings` endpoint, the ten consumer rewirings,
and a "Runtime settings" section on the System page.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / ARQ; React 19 / TypeScript / Mantine v9.

Reference spec: `docs/superpowers/specs/2026-06-14-configurable-tunables-design.md`.

---

## PR1 — Part A: boot-time `.env` settings  (branch `feat/tunables-env`)

### Task 1: Add the four boot-time `Settings` fields

**Files:**
- Modify: `backend/app/core/config.py` (the `Settings` class)
- Test: `backend/tests/test_tunables_config.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_tunables_config.py
from app.core.config import Settings


def _settings(**env):
    # _env_file=None: ignore any real .env so defaults are exercised deterministically
    return Settings(_env_file=None, database_url="x", session_secret="x", master_key="x", **env)


def test_boot_time_defaults_match_current_behavior():
    s = _settings()
    assert s.worker_max_jobs == 10
    assert s.db_pool_size == 5
    assert s.db_max_overflow == 10
    assert s.opnsense_http_timeout == 10.0


def test_boot_time_overrides_from_env(monkeypatch):
    monkeypatch.setenv("WORKER_MAX_JOBS", "4")
    monkeypatch.setenv("DB_POOL_SIZE", "20")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("OPNSENSE_HTTP_TIMEOUT", "7.5")
    s = _settings()
    assert (s.worker_max_jobs, s.db_pool_size, s.db_max_overflow, s.opnsense_http_timeout) == (
        4, 20, 0, 7.5
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_tunables_config.py -q`
Expected: FAIL (`AttributeError`/validation — fields don't exist yet).

- [ ] **Step 3: Add the fields**

In `backend/app/core/config.py`, inside `Settings` (group near the worker/pool area, after `redis_url`):

```python
    # Boot-time deploy tuning (requires restart). See .env.example "Boot-time tuning".
    worker_max_jobs: int = 10  # ARQ worker concurrency (>=1)
    db_pool_size: int = 5  # SQLAlchemy engine pool size, API + worker (>=1)
    db_max_overflow: int = 10  # SQLAlchemy pool overflow beyond pool_size (>=0)
    opnsense_http_timeout: float = 10.0  # default per-request connector timeout, seconds (>0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_tunables_config.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/test_tunables_config.py
git commit -m "feat(config): add boot-time tuning settings (worker/db-pool/connector-timeout)"
```

### Task 2: Wire `worker_max_jobs` into `WorkerSettings`

**Files:**
- Modify: `backend/app/worker.py` (`class WorkerSettings`, ~line 559)
- Test: `backend/tests/test_worker_config.py` (add a case)

- [ ] **Step 1: Write the failing test** (append to `test_worker_config.py`)

```python
def test_worker_settings_max_jobs_from_settings():
    from app.core.config import get_settings
    from app.worker import WorkerSettings

    assert WorkerSettings.max_jobs == get_settings().worker_max_jobs
```

- [ ] **Step 2: Run it** — `cd backend && python -m pytest tests/test_worker_config.py -q` → FAIL (`AttributeError: max_jobs`).

- [ ] **Step 3: Add the attribute** to `class WorkerSettings` (alongside `redis_settings`):

```python
    max_jobs = _settings.worker_max_jobs  # worker concurrency (.env: WORKER_MAX_JOBS)
```

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `feat(worker): honor WORKER_MAX_JOBS for ARQ concurrency`.

### Task 3: Wire the pool args into `make_engine`

**Files:**
- Modify: `backend/app/core/db.py` (`make_engine`, line 14-15)
- Test: `backend/tests/test_tunables_config.py` (add)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_make_engine_applies_pool_settings(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    from app.core import config, db

    config.get_settings.cache_clear()
    engine = db.make_engine("postgresql+asyncpg://u:p@localhost/x")
    assert engine.pool.size() == 7
    assert engine.pool._max_overflow == 3
    config.get_settings.cache_clear()
```

- [ ] **Step 2: Run it** → FAIL (default size 5, overflow 10).

- [ ] **Step 3: Implement** in `db.py`:

```python
def make_engine(url: str) -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
    )
```

- [ ] **Step 4: Run it** → PASS. Also run `tests/test_db_connect.py` to confirm no regression.

- [ ] **Step 5: Commit** — `feat(db): size the SQLAlchemy pool from DB_POOL_SIZE/DB_MAX_OVERFLOW`.

### Task 4: Resolve the connector timeout default from settings

**Files:**
- Modify: `backend/app/connectors/opnsense/client.py` (`__init__`, ~line 98-106)
- Test: `backend/tests/test_opnsense_client.py` (add)

- [ ] **Step 1: Write the failing test** (append)

```python
def test_client_timeout_defaults_to_setting(monkeypatch):
    monkeypatch.setenv("OPNSENSE_HTTP_TIMEOUT", "3.5")
    from app.core import config
    from app.connectors.opnsense.client import OpnsenseClient

    config.get_settings.cache_clear()
    c = OpnsenseClient("https://box.example", "k", "s")
    assert c._timeout == 3.5
    # explicit override still wins
    c2 = OpnsenseClient("https://box.example", "k", "s", timeout=1.0)
    assert c2._timeout == 1.0
    config.get_settings.cache_clear()
```

- [ ] **Step 2: Run it** → FAIL (`_timeout == 10.0`).

- [ ] **Step 3: Implement** — change the signature default to `None` and resolve in the body:

```python
        timeout: float | None = None,
        ...
    ) -> None:
        ...
        from app.core.config import get_settings
        self._timeout = timeout if timeout is not None else get_settings().opnsense_http_timeout
```

(Import `get_settings` at module top instead of inline if no circular-import issue; verify with the
test suite.)

- [ ] **Step 4: Run it** → PASS. Run the full `tests/test_opnsense_client.py` to confirm no regression.

- [ ] **Step 5: Commit** — `feat(connector): default OPNsense client timeout from OPNSENSE_HTTP_TIMEOUT`.

### Task 5: Comprehensive `.env.example`

**Files:**
- Modify: `.env.example` (root) — add the two new commented sections
- Test: `backend/tests/test_tunables_config.py` (add a doc-parity guard)

- [ ] **Step 1: Write the failing test** (append) — guard that every new boot-time key is documented:

```python
import pathlib


def test_env_example_documents_boot_time_keys():
    text = pathlib.Path(__file__).resolve().parents[2].joinpath(".env.example").read_text()
    for key in ("WORKER_MAX_JOBS", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "OPNSENSE_HTTP_TIMEOUT"):
        assert key in text, f"{key} missing from .env.example"
```

- [ ] **Step 2: Run it** → FAIL (keys absent).

- [ ] **Step 3: Add a "Boot-time tuning (requires restart)" section** to `.env.example` documenting the
  four new keys (with the validity ranges) plus the already-supported boot-time cadences, and a
  "Runtime defaults (initial value; then editable from the System page)" section listing the ten
  runtime keys. Use the existing comment style.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `docs(env): comprehensive .env.example (boot-time tuning + runtime defaults)`.

### PR1 wrap-up
- [ ] Run full backend suite + ruff: `cd backend && python -m pytest -q && ruff check app/`.
- [ ] Push branch, open PR, green CI, squash-merge.

---

## PR2 — Part B backend: runtime registry + endpoint + consumer rewiring  (branch `feat/tunables-runtime-api`)

> Expand each into bite-sized TDD tasks at execution time. Code shapes are fixed by the spec.

- **Task A:** Add `Settings` fields `firmware_max_status_polls: int = 360`, `firmware_poll_interval_seconds: float = 5.0`; keep the `firmware_action.py` constants pointing at them (or read at use). Test defaults.
- **Task B:** `app/services/runtime_settings.py` — the `RUNTIME_SETTINGS` registry (list of entries:
  `key`, `kind`, `default: lambda s`, `min`, `max`), plus `get_runtime_config(session)` (merge DB
  override row over registry defaults → typed dict) and `update_runtime_config(session, patch)`
  (validate kind/bounds/unknown-key → write merged → return effective). Stored under one `app_setting`
  key `"runtime_config"`. Unit-test: defaults when no row; override merge; reject unknown key / wrong
  type / out-of-bounds (ValueError).
- **Task C:** `app/schemas/system.py` + `app/api/system.py` — `GET /api/admin/settings`
  (effective + default + kind + min/max per key) and `PUT /api/admin/settings` (CSRF-guarded,
  `Action.SYSTEM_MANAGE`, audit `action="system.runtime_config"`). API tests: RBAC (non-superadmin
  403), patch round-trips, audit row written, 422 on invalid.
- **Task D (consumers — wire each to `get_runtime_config`):**
  - Firmware polls → `firmware_action.run_firmware_action` reads + passes into `poll_until_done`.
  - `silent_alert_enabled`/`after_hours` → `silent_alerts.detect_*` (has session).
  - `catalog_auto_fetch`/`geoip_auto_fetch` → thread the runtime value into the catalog/geoip providers.
  - `session_ttl_hours` → `app/api/auth.py` login handler; `session_idle_minutes` →
    `app/services/auth.py` `AuthSessionService` (has `self.session`).
  - `login_max_attempts`/`login_lockout_window_seconds` → extend `SlidingWindowLimiter.check(...)` with
    optional `max_attempts`/`window_seconds` overrides (DO NOT recreate the singleton — preserve window
    state); login handlers read runtime-config and pass them.
  - Each consumer gets a test asserting an override row changes the observed behavior.
- **Task E:** full suite + ruff; push, PR, green CI, squash-merge.

---

## PR3 — Part B frontend: System page "Runtime settings" section  (branch `feat/tunables-system-page`)

> Expand at execution time.

- **Task A:** `npm run gen:api` to pick up the new `/api/admin/settings` endpoint types.
- **Task B:** A `RuntimeSettings` section component on the System page: fetch effective config, render a
  grouped form (per registry group), show effective value + default + reset-to-default, PUT on save via
  the typed client + react-query invalidation. Reuse existing System-page form patterns
  (`live_push`/`mfa`).
- **Task C:** i18n — add the keys to `frontend/src/i18n/en.ts` first, then mirror into all 12 locales
  (`it es fr de pt nl ru ar zh zhTW ja`) — build-gated.
- **Task D:** Vitest for the section (renders values, save calls the client). Run the **build gate**:
  `cd frontend && npm run build` (tsc -b + vite build), `npm test`, `npm run lint`.
- **Task E:** push, PR, green CI, squash-merge. Tag a version (milestone complete) +
  CHANGELOG entry per the release process.

---

## Self-review notes
- Defaults across all four boot-time fields and ten runtime fields equal today's behavior (verified
  against `config.py` + the `firmware_action.py` constants).
- No security logic changes — only the *source* of each value (env/DB vs hardcoded). The login limiter
  keeps its in-process state; only thresholds re-read.
- Pool args apply to both engines (both built via `make_engine`).
- The connector timeout is boot-time (not runtime) — see spec rationale (16 SSRF construction sites).

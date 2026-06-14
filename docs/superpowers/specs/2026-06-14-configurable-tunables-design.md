# Configurable deployment tunables — design

**Status:** approved (brainstorm 2026-06-14) · **Type:** infrastructure / ops ergonomics

## Problem

Several operational knobs are currently hardcoded or only settable by editing source:

- The ARQ worker concurrency (`max_jobs`) is unset → ARQ's default of 10. An operator who wants
  a bigger fleet to drain faster (or a smaller box to stay calm) cannot change it without code.
- The SQLAlchemy engine pool is the library default (5 + 10 overflow) with no way to size it for
  the deployment.
- The OPNsense connector HTTP timeout, the firmware-poll cadence, the silent-tenant thresholds, the
  login brute-force limits, the session lifetimes, and the catalog/geoip auto-fetch switches are all
  baked in (some as `Settings` fields with no surfaced control, some as module constants).

MSP operators deploy OPNGMS in very different environments and want to tune these without forking.

## Goal

Make deployment tunables configurable along two axes, each matched to how the value is actually read:

1. **Boot-time** values (read once at startup; a restart is acceptable and expected to change them) →
   driven from `.env`.
2. **Runtime-safe** values (read at use-time on every request/job) → editable live from the superadmin
   **System** page, with `.env` (or the code default) providing the initial default.

Defaults must preserve today's exact behavior. No security logic changes — only the *source* of a value.

## Boot-time vs runtime (the dividing line)

- **Boot-time → `.env` only:** anything read once at process start. Worker `max_jobs`, the DB pool, the
  cron cadences (`WorkerSettings.cron_jobs` reads `_settings.X` at module import), redis/DB URLs,
  secrets, catalog/geoip base URLs, syslog receiver host/port, `opensearch_url`, **and the OPNsense
  connector HTTP timeout** (see the note below).
- **Runtime-safe → System page:** per-operation values and master switches/policies that are re-read
  on each use.

### Why the connector timeout is boot-time, not runtime

The approved brainstorm tentatively listed `opnsense_http_timeout` as a runtime setting. Grounding in
the code showed the `OpnsenseClient(...)` constructor is invoked at **~16 sites** (10 API/service
modules + 6 inside `app/worker.py`), with no shared factory. `OpnsenseClient` is the SSRF-guarded
outbound boundary (invariant #4). Making the timeout runtime-tunable would require threading a DB read
through all 16 construction sites or a risky refactor of that security boundary, for a value that is
really a **deploy/network characteristic** (latency to the managed boxes). So it moves to `.env`
(Part A): the `OpnsenseClient` default reads `get_settings().opnsense_http_timeout` — a one-line change,
zero call-site edits. It stays fully configurable, in the place that fits it.

## Architecture

### Part A — `.env` (boot-time)

Add four env-backed fields to `app/core/config.py:Settings` (defaults = current behavior):

| Field | Default | Wired into | Validity |
|-------|---------|-----------|----------|
| `worker_max_jobs` | `10` | `app/worker.py` `WorkerSettings.max_jobs = _settings.worker_max_jobs` | `>= 1` |
| `db_pool_size` | `5` | `app/core/db.py` `make_engine()` `pool_size=` | `>= 1` |
| `db_max_overflow` | `10` | `app/core/db.py` `make_engine()` `max_overflow=` | `>= 0` |
| `opnsense_http_timeout` | `10.0` | `OpnsenseClient.__init__` default reads `get_settings()` | `> 0` |

`make_engine()` applies the pool args to **both** the API engine (`opngms_app`, RLS) and the worker
engine (owner) — both go through `make_engine`. The validity ranges are documented as comments in the
`Settings` class (matching the existing `# (1..30)`-style annotations); pydantic does not hard-enforce
them, consistent with the surrounding fields.

Also ship a **comprehensive `.env.example`** at the repo root (with a `backend/.env.example` companion
if the build expects one), organized into three commented sections:

1. **Required secrets** — `DATABASE_URL`/`ADMIN_DATABASE_URL`/`APP_ROLE_PASSWORD`/`POSTGRES_PASSWORD`,
   `SESSION_SECRET`, `MASTER_KEY`, … (the fail-closed `change-me` guard applies here).
2. **Boot-time tuning ("requires restart")** — the four new fields, the cron cadences, redis/opensearch
   URLs, syslog receiver, pool, etc.
3. **Runtime defaults ("initial value; then editable from the System page")** — the ten runtime settings
   below, documented as the *initial default* the System page starts from.

### Part B — runtime via a small generic registry

Extend the **existing** infra rather than writing ten bespoke get/set pairs:
`app/models/app_setting.py` (key → JSONB value) + `app/services/app_settings.py` + `app/api/system.py`
(`/api/admin/*`, superadmin-gated by `Action.SYSTEM_MANAGE`, audited). Today that infra hosts
`mfa_required` and `live_push_enabled` with the idiom `get_X(session, *, env_default=get_settings().X)`.

New pieces:

- A declarative **`RUNTIME_SETTINGS`** registry. Each entry: `key`, `kind` (`int`/`float`/`bool`),
  `default` source (`lambda s: s.<field>` reading `Settings`), and `min`/`max` bounds for the numeric
  ones. This is the single source of truth for names, types, defaults, and validation.
- **One** `app_setting` row, key `"runtime_config"`, whose JSONB value holds only the operator's
  overrides (absent keys ⇒ use the env/code default).
- `get_runtime_config(session) -> dict` — load the overrides row, merge over the registry defaults,
  return a fully-typed, validated dict (every key present, coerced to its `kind`).
- `update_runtime_config(session, patch: dict) -> dict` — validate each patched key against the
  registry (known key, correct type, within bounds), write the merged overrides back, return the new
  effective config. Unknown keys or out-of-range values raise `ValueError` (→ HTTP 422).
- **One** endpoint pair on the existing system router:
  - `GET /api/admin/settings` → `{ settings: [{key, value, default, kind, min, max}], }` (effective
    value + default, so the UI can render "reset to default").
  - `PUT /api/admin/settings` (CSRF-guarded, audited `action="system.runtime_config"`) → applies a
    partial patch, returns the new effective config.
- The **System** page gains a **"Runtime settings"** section: a grouped form showing each setting's
  effective value, its default, and a reset control; `live_push` and `mfa` stay exactly as they are.

### The ten runtime settings

All ten resolve to a `Settings` field that supplies the default. **The consumer wiring is NOT a uniform
one-line swap** — the brainstorm assumed it was, but a code audit found three distinct shapes. Each row
below names its real consumer and wiring effort.

| Group | Setting | Default | Consumer + wiring (audited) |
|-------|---------|---------|------------------------------|
| Firmware | `firmware_max_status_polls` | `360` | `firmware_action.run_firmware_action` (has `session`) reads runtime-config, passes `max_polls`/`interval` into `poll_until_done`. **Easy.** New `Settings` field (today the constant `MAX_STATUS_POLLS`). |
| Firmware | `firmware_poll_interval_seconds` | `5.0` | same call path. **Easy.** New `Settings` field (today `POLL_INTERVAL`). |
| Distribution | `catalog_auto_fetch` | `true` | `catalog_provider.get_catalog`/`get_plugins_catalog` take a `settings` arg today; thread the runtime value (call sites have a session). **Medium** (signature/threading). |
| Distribution | `geoip_auto_fetch` | `true` | `geoip_provider` (`geoip_provider.py:111`), same threading. **Medium.** |
| Maintenance | `silent_alert_enabled` | `true` | `silent_alerts.detect_*` (cron body, has `session`). **Easy.** |
| Maintenance | `silent_alert_after_hours` | `6` | same. **Easy.** |
| Security (login) | `login_max_attempts` | `5` | `app/api/auth.py:36` builds a **module-level singleton** `login_limiter = SlidingWindowLimiter(...)` at import. Do **not** recreate per request (it holds the sliding-window state). Instead extend `SlidingWindowLimiter.check(...)` with optional `max_attempts`/`window_seconds` overrides; the login handlers (which have a `session`) read runtime-config and pass them to the two `check()` calls. **Medium** (limiter API tweak + 2 call sites). |
| Security (login) | `login_lockout_window_seconds` | `900` | same `check()` override path. **Medium.** |
| Security (session) | `session_ttl_hours` | `12` | `app/api/auth.py:120/252/265` (login handler, has `session`). **Easy** (swap `settings.X` → runtime read). |
| Security (session) | `session_idle_minutes` | `120` | `app/services/auth.py:70/109` (`AuthSessionService`, has `self.session`). **Easy.** |

**New `Settings` fields needed:** `firmware_max_status_polls` and `firmware_poll_interval_seconds` —
today they are the module constants `MAX_STATUS_POLLS = 360` / `POLL_INTERVAL = 5.0` in
`app/services/firmware_action.py`. Add them to `Settings` (defaults equal to today's constants) so the
registry has a default source. The other eight already exist in `Settings`. `MAX_UPGRADE_STEPS = 6`
stays a constant (out of scope).

**Consumer audit note:** the cron *cadence* fields (`silent_alert_cron_minute`, `ingest_every_minutes`,
…) stay boot-time — only the silent-alert *threshold/switch* values become runtime. The login limiter
keeps its in-process window **state** across a runtime change; only the thresholds re-read. Reading
runtime-config adds one cached-ish `app_setting` row lookup per consumer call; all the consumers above
already hold a `session`, so no new DB dependency is introduced into a path that lacked one.

## Decomposition — three PRs

1. **PR1 (small, boot-time):** Part A — the four `.env` `Settings` fields + their wiring
   (`worker.py`, `db.py`, `OpnsenseClient`) + the comprehensive `.env.example`. Tests:
   `WorkerSettings.max_jobs` honors the field; `make_engine` passes the pool args; the client default
   reads the setting.
2. **PR2 (backend runtime):** Part B backend — the two new `Settings` fields, the `RUNTIME_SETTINGS`
   registry, `get_runtime_config` / `update_runtime_config`, the `GET`/`PUT /api/admin/settings`
   endpoint + schemas, and the ten consumer rewirings. Tests: registry validation (type/bounds/unknown
   key), merge-over-default, endpoint RBAC + audit, and each consumer reading the override.
3. **PR3 (frontend runtime):** the System page "Runtime settings" section + the typed API client
   regen + i18n keys across all 12 locales + tests.

## Out of scope (future)

- Per-tenant overrides of any runtime setting (these are global/superadmin only).
- Making cron cadences runtime-editable (they are import-time; would need worker reload).
- A generic typed-settings UI widget library beyond what this section needs.

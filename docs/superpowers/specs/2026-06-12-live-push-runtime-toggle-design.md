# Runtime toggle for live config push (C8) — design spec

**Date:** 2026-06-12
**Status:** Approved (batch directive); building.

## Goal
Today `live_push_enabled` (the master switch that flips config-push from dry-run to real device writes)
is an **env var** — changing it needs a redeploy. Make it a **runtime, superadmin-controlled toggle**
(DB-backed via `app_settings`), with the env value as the initial default. A superadmin can flip live
push on/off from the UI without a restart.

## Decisions
- DB-backed via the existing `app_settings` key/value table (key `live_push_enabled`, value
  `{"enabled": bool}`) — same pattern as the MFA policy.
- The env `live_push_enabled` (config.py) becomes the **fallback default** when no DB row exists, so
  existing env-based deployments behave unchanged until a superadmin sets it.
- Gated by a new org-level action `SYSTEM_MANAGE` (superadmin-only).

## Components
1. **RBAC** (`app/core/rbac.py`): add `SYSTEM_MANAGE = "system.manage"` to `Action` + `_ORG_ACTIONS`.
2. **Service** (`app/services/app_settings.py`): mirror the MFA helpers —
   - `async def get_live_push(session, *, env_default: bool) -> bool` (reads key `live_push_enabled`,
     returns `value["enabled"]` or `env_default`).
   - `async def set_live_push(session, enabled: bool) -> None` (upsert).
3. **config-push read** (`app/services/config_push.py`): replace `live = get_settings().live_push_enabled`
   with `live = await get_live_push(session, env_default=get_settings().live_push_enabled)`.
4. **API** (`app/api/system.py`, new router, mounted in `main.py`): mirror the MFA-policy endpoints —
   - `GET /api/admin/live-push` (`require_org(SYSTEM_MANAGE)`) → `LivePushOut{enabled}` (passes the env
     default to the service).
   - `PUT /api/admin/live-push` (`require_org(SYSTEM_MANAGE)`, `enforce_csrf`) body `LivePushIn{enabled}`
     → sets it, audits `system.live_push` (actor=user, details={"enabled": …}), returns `LivePushOut`.
5. **Schemas** (`app/schemas/system.py`): `LivePushIn{enabled: bool}`, `LivePushOut{enabled: bool}`.
6. **Frontend** (`frontend/src/pages/SystemSettingsPage.tsx` + hook): a superadmin page with a Mantine
   `Switch` ("Live config push to devices") bound to `GET/PUT /api/admin/live-push`, with a clear
   warning that ON means real writes hit devices. Nav item `me?.is_superadmin && /admin/system` +
   route, mirroring the SMTP page. i18n `nav.system`.

## Error handling / security
- Superadmin-only (org-gated); PUT is CSRF-protected + audited. The toggle changes no tenant data; it
  flips a global mode the worker reads per apply. No secrets. Default-OFF safety preserved (env default
  is False; absent DB row → env default).

## Testing
- **Service:** `get_live_push` returns the env default when unset, the stored value when set; `set_live_push` upserts.
- **config-push:** with the DB toggle OFF (or unset+env False) apply is dry-run; with it ON, apply goes live (assert via the existing config-push test seam / `apply_for_kind(dry_run=...)`).
- **API:** superadmin GET/PUT round-trips + audit; non-superadmin → 403; PUT without CSRF → 403.
- **Frontend (vitest+MSW):** the switch reflects GET and PUTs on toggle; non-superadmin doesn't see the nav.

## Out of scope
- Per-tenant or per-device live-push scoping (it stays a global master switch). Other system toggles
  (this just establishes the `SYSTEM_MANAGE` + `/admin/system` home for future ones).

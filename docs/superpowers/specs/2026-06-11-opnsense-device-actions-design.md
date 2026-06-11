# OPNsense Device Actions — Firmware Update/Upgrade + Plugin Install/Remove + WebGUI Link — Design Spec

**Date:** 2026-06-11
**Status:** Approved (design)
**Builds on:** the device connector + the config-push scheduling pattern (`schedule_config_change` → `enqueue(task, id, defer_until=scheduled_at)` + an ARQ worker). The connector is `(edition, version)`-aware and resolver-driven; this milestone adds **action** endpoints (mutations that can reboot the firewall) that are NOT part of the read/telemetry matrix.

## Goal

From the OPNGMS console, trigger four device actions — **firmware package update**, **major release upgrade**, **plugin install**, **plugin remove** — each **now or scheduled**, tracked to completion (reboot-tolerant), behind an explicit UI confirmation; plus a **WebGUI deep-link** button. (True WebGUI SSO is a separate milestone.)

## Architecture

All four actions share one mechanism: a `firmware_actions` record (kind + optional target + scheduled_at + status) created by an API endpoint, enqueued via the existing `defer_until` scheduling, and run by one worker task `run_firmware_action`. The worker POSTs the OPNsense firmware operation and polls `core/firmware/upgradestatus` to completion, tolerating reboots (the box goes unreachable and comes back). The WebGUI button is frontend-only (opens `device.base_url`).

## Tech Stack

Python 3.14, the SSRF-guarded connector, SQLAlchemy/Alembic, ARQ worker, pytest + respx (backend); React/Mantine/TanStack Query + Vitest/RTL/MSW (frontend).

---

## 1. OPNsense firmware action API + two real-world constraints

Endpoints (core; verified shapes confirmed live read-only during implementation):
- `POST core/firmware/check` — trigger a mirror check.
- `GET/POST core/firmware/status` — `status`, `updates` (count), `download_size`, `upgrade_needs_reboot`, and the new-major indicator (`product_latest` vs `product_version`).
- `POST core/firmware/update` — apply all available **package** updates (may reboot).
- `POST core/firmware/upgrade` — **major release** upgrade (always reboots).
- `POST core/firmware/install/<pkg>` / `POST core/firmware/remove/<pkg>` — install / remove a plugin.
- `GET core/firmware/upgradestatus` — `{status, log}`: progress of any running firmware op (`running` → `done`).

**Constraint 1 — plugin install requires up-to-date firmware.** OPNsense deliberately blocks plugin installs while a firmware update is pending (the plugin repo is pinned to the running firmware). So a `plugin_install` action must pre-check `firmware/status`; if updates are pending it **fails with a clear reason** ("device must be updated to the latest version before installing plugins") and performs no install. (Refs: OPNsense forum "Why can't I install plugins when there is an update available?"; firmware manual.)

**Constraint 2 — a major upgrade is multi-step + multi-reboot.** Reaching a new major usually requires: update the current series to its latest → reboot → then the major upgrade → reboot, sometimes across several stages. So the `firmware_upgrade` worker is a **loop**: repeatedly (`check` → if an upgrade is offered run `upgrade`, else run `update` → wait for the op + any reboot) until `firmware/status` reports up-to-date with no upgrade offered, bounded by `MAX_UPGRADE_STEPS = 6` and a total budget (`UPGRADE_BUDGET_SECONDS = 3600`). (Refs: OPNsense updates manual; Thomas-Krenn upgrade guide.)

## 2. Connector methods (`client.py`)

Action methods (POST, long-running; not in the telemetry matrix — stable core endpoints):
- `firmware_check() -> dict` — POST `core/firmware/check`.
- `firmware_status_raw() -> dict` — the raw `core/firmware/status` (updates/size/needs_reboot/new-major). (Distinct from the existing normalized `get_firmware_status`.)
- `firmware_update() -> dict` — POST `core/firmware/update`.
- `firmware_upgrade() -> dict` — POST `core/firmware/upgrade`.
- `plugin_install(name) -> dict` — POST `core/firmware/install/<name>` (name validated against `^[A-Za-z0-9._-]+$` before being put in the path).
- `plugin_remove(name) -> dict` — POST `core/firmware/remove/<name>` (same validation).
- `firmware_upgrade_status() -> dict` — GET `core/firmware/upgradestatus`.
Each POST uses the long `RECONFIGURE_TIMEOUT`-style timeout (these are slow). Plugin name validation refuses anything outside the charset (no path injection into the URL).

## 3. Model + migration 0018

`firmware_actions` table: `id`, `tenant_id`, `device_id` (FK), `created_by`, `kind` (`firmware_update` | `firmware_upgrade` | `plugin_install` | `plugin_remove`), `target` (plugin name; `""` for firmware), `scheduled_at` (nullable), `status` (`scheduled` | `running` | `done` | `failed`), `result` (JSONB: log/version/steps/error), `created_at`, `applied_at` (nullable). Tenant-scoped + RLS like the other tenant tables. Migration **0018** (down_revision 0017).

## 4. API (`api/firmware.py`, RBAC like config-push: admin/operator)

- `POST /devices/{id}/firmware/check` → run `firmware_check` + `firmware_status_raw`; return a secret-safe summary `{status, updates, download_size, needs_reboot, new_major}`. (Synchronous, read-ish; no record.)
- `POST /devices/{id}/firmware/action` body `{kind, target?, scheduled_at?}` → validate (target required+charset-checked for plugin ops; forbidden otherwise); create a `firmware_actions` row (`scheduled`); `enqueue("run_firmware_action", str(action.id), defer_until=scheduled_at)` (now → `None`). Returns the action.
- `GET /devices/{id}/firmware/actions` → list this device's actions (status + result summary).

## 5. Worker `run_firmware_action(ctx, action_id)`

Per-device advisory lock (reuse `config_push._advisory_key`) so two actions don't run concurrently. Load action+device+client; set `set_identity` from the device; `status="running"`.

Dispatch by `kind`:
- **`plugin_remove`** → `plugin_remove(target)` → `_poll_until_done(client)`.
- **`plugin_install`** → pre-check: `firmware_check()` then `firmware_status_raw()`; if updates are pending → `status="failed"`, `result={"error":"firmware not up to date; update the device first"}`, return. Else `plugin_install(target)` → `_poll_until_done`.
- **`firmware_update`** → `firmware_update()` → `_poll_until_done` (reboot-tolerant).
- **`firmware_upgrade`** → the multi-step loop:
  ```
  steps = []
  for i in range(MAX_UPGRADE_STEPS):
      await client.firmware_check()
      st = await client.firmware_status_raw()
      if _up_to_date(st):           # no pending updates AND no newer major offered
          break
      if _major_offered(st):
          await client.firmware_upgrade()
      else:
          await client.firmware_update()
      steps.append(await _poll_until_done(client))   # waits through any reboot
  else:
      raise OpnsenseError("upgrade did not converge within MAX_UPGRADE_STEPS")
  ```
On success: re-detect identity (`get_device_identity`) → record the new firmware version + `device.firmware_version/edition/series`; `status="done"`, `result={"steps":..., "version":...}`. On `OpnsenseError`/budget exceeded → `status="failed"` with a sanitized reason.

`_poll_until_done(client)` — reboot-tolerant: loop (a) try `firmware_upgrade_status()`: if it reports the op finished (`status` not `running` / log shows completion) → return; (b) if the connection drops (`ReachabilityError` — the box rebooted), switch to polling `test_connection()` until it succeeds again (the box is back) or `REBOOT_TIMEOUT = 900`s elapses; on success, resume status polling; (c) overall bounded by `UPGRADE_BUDGET_SECONDS`. Uses short async sleeps between polls. (Exact `upgradestatus` field names confirmed live read-only during implementation.)

## 6. Frontend (`DeviceDetailPage.tsx` + components)

- **WebGUI button** — `<a href={device.base_url} target="_blank" rel="noopener noreferrer">Open WebGUI ↗</a>` (also a small icon-button in the device row). No backend. (Tooltip notes it opens the device login; SSO is a later milestone.)
- **Firmware section** — "Check for updates" (calls the check endpoint, shows `updates` / `download_size` / reboot-needed / new-major); "Update now" and "Schedule update…"; "Major upgrade now" and "Schedule upgrade…". Each destructive action goes through the existing `ConfirmModal` with an explicit warning ("This will update/REBOOT the firewall; it may go offline. A major upgrade runs several reboots.").
- **Plugins section** — the installed/available plugins (from `get_plugin_info`/`firmware/info`); per-plugin **Install** / **Remove** (now or schedule) behind `ConfirmModal`; the Install modal warns it requires the device to be up to date.
- **Actions status** — a list of recent `firmware_actions` with live status (TanStack Query polling the `GET .../firmware/actions` while any action is `running`/`scheduled`).
- New hooks in `src/api/` (e.g. `firmwareHooks.ts`) using the typed openapi-fetch client + the CSRF token, following the existing `configHooks`/`reportHooks` pattern.

## 7. Safety

- **Explicit UI confirmation** (`ConfirmModal`) on every destructive action; the deliberate create+schedule is itself the gate. **No master switch** (per decision).
- **Per-device advisory lock** — one firmware action at a time per device.
- **Reboot tolerance** — the box going unreachable during a reboot is expected, not a failure; only a `REBOOT_TIMEOUT`/budget overrun marks `failed`.
- Plugin name charset-validated before being placed in the request path.
- RBAC: only admin/operator may trigger actions (same guard as config-push).

## 8. Error handling

- Connector/transport error or `upgradestatus` never completing within budget → `status="failed"` with a sanitized reason; no secret leakage in `result`.
- `plugin_install` with pending firmware updates → `failed` ("update first"), no install.
- Unknown `kind` / missing `target` for a plugin op → 4xx at the API (rejected before enqueue).
- The advisory lock not acquired (a concurrent action holds the per-device lock) → the worker bails **without raising**, so the row stays `scheduled` but is **not auto-retried** by ARQ and must be re-enqueued to run. This mirrors config-push and trades guaranteed delivery for strict per-device serialization. *Follow-up:* a sweeper that re-enqueues orphaned `scheduled` actions (shared with config-push) is tracked, not built.

## 9. Testing

- **Connector (respx):** `firmware_check`/`status_raw`/`update`/`upgrade`/`upgradestatus`; `plugin_install`/`remove` hit `install/<name>`/`remove/<name>` with name validation (reject `../`, spaces).
- **Worker:** `plugin_remove` happy path; `plugin_install` pre-check blocks when updates pending; `firmware_update` reboot-tolerant poll (mock: running→done; and unreachable-then-back); **`firmware_upgrade` multi-step loop** (mock: check shows updates → update → reboot/back → check shows major → upgrade → reboot/back → check up-to-date → done; and the non-convergence → failed within MAX steps).
- **API:** check returns summary; action create → row + `enqueue(defer_until)`; list; plugin op requires target; RBAC.
- **Migration 0018** + RLS.
- **Frontend:** WebGUI link renders with the right href/rel; check shows the summary; action buttons open the ConfirmModal and POST; status list polls.
- **Live verification:**
  - **Read-only live:** `firmware_check` / `firmware_status_raw` / `firmware_upgrade_status` against the box.
  - **Destructive live — plugins only (safe, no reboot):** install a harmless throwaway plugin (e.g. a small `os-*`) → confirm it appears → remove it (guaranteed cleanup). Verifies the install/remove + upgradestatus paths on real hardware.
  - **Firmware update/upgrade:** verified via the **mocked worker** only — NOT run against the box (would reboot / change its version).

## 10. Out of scope

- **WebGUI SSO** ("be already inside") — a separate milestone (needs stored WebGUI credentials + server-side login + a reverse-proxy of the admin UI, or an external IdP).
- Automatic firmware rollback; pinning a specific target version for `upgrade`; auto-chaining "update then install" for plugins (v1 surfaces the precondition and fails clearly instead).

## 11. File structure

- **Create:** `backend/app/models/firmware_action.py`, `backend/migrations/versions/0018_firmware_actions.py`, `backend/app/api/firmware.py`, `backend/app/services/firmware_action.py` (the worker body + `_poll_until_done` + `_up_to_date`/`_major_offered` helpers), `scripts/verify_plugin_live.py` (throwaway-plugin e2e), backend tests, `frontend/src/api/firmwareHooks.ts`, frontend components (firmware + plugins sections, WebGUI button) + tests.
- **Modify:** `backend/app/connectors/opnsense/client.py` (the firmware action methods), `backend/app/worker.py` (`run_firmware_action` + register it in `WorkerSettings.functions`), `backend/app/main.py`/router wiring for `api/firmware.py`, `frontend/src/pages/DeviceDetailPage.tsx` (+ device row for the WebGUI button).
- **Unchanged:** the read/telemetry matrix, the config-push pipeline, the advisory-lock helper (reused).

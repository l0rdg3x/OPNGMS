# Apply-Pipeline Reliability — Design Spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Covers:** TODO items #1 (orphaned/stuck scheduled-action sweeper) and #2 (operator-triggered config-push revert) — designed together with the user as one cohesive "apply-pipeline reliability" milestone.

## Goal

Close two reliability gaps in the device-action apply pipeline:

1. **No auto-retry for lock-miss orphans.** Both `apply_config_change` and `run_firmware_action` take a per-device `pg_try_advisory_xact_lock`; on a lock miss they return the unchanged status **without raising**, so ARQ never retries and the row stays `scheduled` forever. There is also no handling for rows stuck `applying`/`running` after a worker crash.
2. **No rollback of a config push.** A pre-apply snapshot is captured, but reverting a change is entirely manual (and OPNsense exposes **no config-restore API** — restore is a GUI operation that reboots).

## Background (verified integration points)

- **Apply pipeline** (`app/services/config_push.py`): `create_change(...)` → `preview_change(change)` → `apply_change(session, change, client, now)`. `apply_change` takes the advisory lock, runs a staleness guard (`baseline_hash` vs live `canonical_hash`), sets `applying`, captures the pre-apply snapshot (when `LIVE_PUSH_ENABLED`), then dispatches via `apply_for_kind(client, kind, operation, payload, dry_run)` → `applied` / `failed` / `conflict`.
- **Kind dispatch** (`app/services/config_apply.py`): `CHANGE_APPLIERS: dict[str, applier]`, `register_change_applier(kind, applier)`. Today: `"alias"` → `client.apply_alias`. Other kinds register their own appliers at startup.
- **`config_change`** statuses: `draft`, `scheduled`, `applying`, `applied`, `failed`, `conflict`. Has `scheduled_at`, `applied_at`, `updated_at`, `result` (JSONB), `pre_apply_snapshot_id`, `baseline_hash`.
- **`firmware_action`** statuses: `scheduled`, `running`, `done`, `failed`. Has `scheduled_at`, `applied_at`, `updated_at`, `result`.
- **Snapshot** (`config_snapshot`): `content_enc = Fernet(gzip(config.xml))` — the full pre-apply config, used to reconstruct pre-apply state for inverse `delete`/`set`.
- **Worker jobs** (`app/worker.py`): `apply_config_change(ctx, change_id)`, `run_firmware_action(ctx, action_id)`; `WorkerSettings.cron_jobs` registers crons. The API enqueues via `app/core/queue.enqueue` with `_defer_until=scheduled_at`.
- **Alerts** (`app/services/alerting.py`): `Alert(tenant_id, device_id, type, label)`.
- **RBAC**: `Action.CONFIG_PUSH` (tenant_admin/operator) gates pushes; writes use `enforce_csrf`.

## Locked decisions (from brainstorming)

1. **Revert is targeted-inverse, operator-triggered (a button), NOT automatic.** It undoes only *our* change (`add`↔`delete`, `set`→pre-apply value), generated as the **inverse `config_change` run through the existing apply pipeline**. No full-config restore (OPNsense has no restore API; full restore reboots + clobbers concurrent changes).
2. **Revert appears on `applied` AND `failed` changes.** The inverse must be tolerant of a partially-applied source (the per-kind apply is idempotent).
3. **Sweeper handles BOTH** lock-miss orphans (re-enqueue) **and** crash-stuck in-progress rows (mark `failed`, do NOT re-run), across **config_changes and firmware_actions**.
4. **Per-pipeline, configurable stuck timeouts** (firmware's set above the worker's max convergence budget to avoid false positives on long-but-healthy upgrades).
5. **Revert v1 implements `firewall_alias`** (the live-verified kind) behind an extensible `INVERSE_BUILDERS` registry; kinds without an inverse → the button is disabled with a reason. **Firmware revert is out of scope** (you cannot un-upgrade).

## Architecture

```
                ┌──────────────────────────────────────────────────────────┐
  cron (5 min)  │ sweep_stuck_actions (owner, RLS-exempt)                   │
  ─────────────▶│  for config_changes + firmware_actions:                  │
                │   • status='scheduled' & overdue  → re-enqueue the job   │──▶ apply_config_change /
                │   • status='scheduled' & too old  → mark 'failed' + alert │    run_firmware_action
                │   • status in (applying|running) & past STUCK_TIMEOUT     │
                │       → mark 'failed (stuck)' + alert (NO re-run)         │
                └──────────────────────────────────────────────────────────┘

  operator ──"Revert"──▶ POST /…/config/changes/{id}/revert
                          invert_change(change, snapshot) → inverse draft
                          (reverts_change_id = id) ──▶ create_change → schedule/apply
                          (same pipeline: preview → lock → snapshot → apply_for_kind → audit)
```

## Component 1 — Sweeper

**New worker cron `sweep_stuck_actions(ctx)`** (`app/worker.py`), registered in `WorkerSettings.cron_jobs` at `minute=set(range(0, 60, sweep_every_minutes))` (default every 5 min). Runs as owner (RLS-exempt), like the other crons. A small pure helper module **`app/services/action_sweeper.py`** holds the row-classification logic so it is unit-testable without ARQ.

For each of the two tables, in **its own transaction per row** (one bad row never aborts the sweep):

| Condition | Action |
|-----------|--------|
| `status='scheduled'` AND `COALESCE(scheduled_at, created_at) < now − ORPHAN_GRACE` | **Re-enqueue** the original job (`apply_config_change` / `run_firmware_action`). Cheap + idempotent: the job re-checks `status=='scheduled'` and re-takes the lock, so re-enqueuing one that is legitimately waiting behind a long op is harmless. |
| `status='scheduled'` AND overdue by `> ORPHAN_MAX_AGE` AND **no in-progress action on the same device** (`applying`/`running`) | **Give up**: `status='failed'`, `result={"error":"orphaned: device lock never acquired"}`, raise an alert. The device-in-progress gate prevents falsely giving up a change that is correctly queued behind a legitimately long-running op (a firmware upgrade can hold the device lock for a while). |
| `status IN ('applying','running')` AND `updated_at < now − STUCK_TIMEOUT[pipeline]` | **Mark stuck**: `status='failed'`, `result={"error":"stuck: in-progress past timeout (worker likely crashed)"}`, raise an alert. **Never re-run** (a partial apply must not be blindly retried). |

- **Heartbeat (enables short, safe timeouts).** A long firmware op legitimately runs for many minutes while holding the device lock, so `updated_at` would otherwise look stale and be falsely flagged "stuck." The firmware worker therefore **heartbeats** `updated_at` on every status/reboot poll via a short-lived **side-channel session** (independent of the lock-holding transaction): a tiny `UPDATE firmware_actions SET updated_at = now() WHERE id = :id`. With a live worker touching the row every poll interval, "no heartbeat for `STUCK_TIMEOUT`" reliably means the worker died — so the timeout can be short. Config-push applies in seconds (no poll loop), so it needs no heartbeat.
- `STUCK_TIMEOUT` is **per pipeline**: `config_stuck_minutes` (default 10) and `firmware_stuck_minutes` (default 15 — safe because of the heartbeat, not because it exceeds the worker budget).
- Alerts reuse `Alert(tenant_id, device_id, type='action_stuck'|'action_orphaned', label=…)`; the sweeper returns a small summary `{re_enqueued, gave_up, marked_stuck}` for logging.
- **New settings** (`app/core/config.py`): `sweep_every_minutes=5`, `orphan_grace_minutes=5`, `orphan_max_age_minutes=60`, `config_stuck_minutes=10`, `firmware_stuck_minutes=15`.

## Component 2 — Operator-triggered Revert (config-push only)

### Inverse builders (`app/services/config_revert.py`)

A registry mirroring `CHANGE_APPLIERS`:

```python
InverseBuilder = Callable[[ConfigChange, str | None], tuple[str, str, dict]]  # (change, pre_apply_config_xml | None) → (operation, target, payload)
INVERSE_BUILDERS: dict[str, InverseBuilder] = {}
def register_inverse_builder(kind, fn): ...
def has_inverse(kind: str) -> bool: ...
def build_inverse(change, snapshot_xml) -> tuple[str, str, dict]: ...  # raises NoInverseError if unregistered
```

Inverse semantics (per builder):
- `operation='add'` → `('delete', target, {minimal identity})` — no snapshot needed.
- `operation='delete'` → `('add', target, <pre-apply definition>)` — reconstructed from the snapshot.
- `operation='set'` → `('set', target, <pre-apply value>)` — reconstructed from the snapshot.

**v1 builder — `firewall_alias`**: `add`→`delete` by alias name (`target`); `delete`/`set`→ re-`add`/`set` the alias as it existed in the pre-apply `config.xml` (parse the `<alias>` subtree by name, reusing the connector's existing XML parsing). The alias apply is idempotent (upsert by name), so reverting a partially-applied change converges.

**Snapshot access**: a helper decrypts + gunzips `config_snapshot.content_enc` → `config.xml` string for the builders. The change's `pre_apply_snapshot_id` (set during a live apply) is the source; if absent (e.g. the source was a dry-run, never live-applied), only `add`→`delete` inverses are possible — `delete`/`set` reverts return a clear "no pre-apply snapshot" error.

### Revert flow

`revert_change(session, change, *, actor_id) -> ConfigChange` (`app/services/config_revert.py`):
1. Guard: `change.status in ('applied','failed')` and `has_inverse(change.kind)`, else 4xx.
2. `inverse_op, inverse_target, inverse_payload = build_inverse(change, snapshot_xml)`.
3. `inverse = await create_change(session, tenant_id=…, device_id=…, created_by=actor_id, kind=change.kind, operation=inverse_op, target=inverse_target, payload=inverse_payload)` — captures a fresh `baseline_hash`, status `draft`.
4. Set `inverse.reverts_change_id = change.id`.
5. The caller then schedules/applies the inverse exactly like a normal change (status `scheduled` → enqueue `apply_config_change`, now or `scheduled_at`), behind `LIVE_PUSH_ENABLED`.

### API (`app/api/config.py`)

`POST /api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/revert` (RBAC `CONFIG_PUSH`, CSRF):
- body: `{ scheduled_at?: datetime }` (omit = now).
- Loads the change (tenant+device scoped), validates revertibility, builds the inverse, creates it, sets `reverts_change_id`, transitions to `scheduled`, enqueues `apply_config_change` (`_defer_until=scheduled_at`), audits `config.change.revert`, returns the inverse change (incl. `preview_change`).
- Mirror the existing schedule/apply endpoint's shape for consistency.

### Data model

Migration **0023**: `config_change.reverts_change_id UUID NULL` FK→`config_changes(id)` `ON DELETE SET NULL` (+ model field). No new status (reverts reuse the normal lifecycle; the link gives traceability). The inverse change's own `result`/`status` reflect its apply.

### Frontend

In the device's **config-changes history** (existing component), add a **"Revert"** button on rows where `status ∈ {applied, failed}` and the kind is invertible. The list response gains `reverts_change_id` and a computed `revertible: bool` (kind has an inverse builder + state is applied/failed). Clicking calls the revert endpoint (now or with a schedule), then refreshes the list; the new inverse row shows a "reverts #…" link. Reuse the existing apply/preview confirm modal pattern. Mantine v9 + Midnight-NOC.

## Data flow summary

- **Sweeper:** cron → classify rows → (re-enqueue | give-up+alert | stuck+alert), each in its own tx.
- **Revert:** button → revert endpoint → `build_inverse` → `create_change(reverts_change_id=…)` → schedule → `apply_config_change` (normal pipeline) → `applied`/`failed`/`conflict`.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Sweeper: a single row errors (DB/enqueue) | caught per-row, logged; the sweep continues with the next row |
| Sweeper: re-enqueued job still lock-misses | stays `scheduled`; re-tried next sweep until `ORPHAN_MAX_AGE`, then given up + alert |
| Stuck firmware that is actually still upgrading | avoided by the **heartbeat**: a live worker bumps `updated_at` every poll, so a healthy long upgrade is never past `firmware_stuck_minutes` |
| Orphan queued behind a legitimately long-running op | not given up: the give-up rule requires **no in-progress action on the device**; it is simply re-enqueued each sweep until the op ahead of it finishes |
| Revert on a non-invertible kind / wrong state | 400/409 with a clear reason; button hidden/disabled |
| Revert of `delete`/`set` with no pre-apply snapshot | 409 "no pre-apply snapshot to reconstruct from" |
| Inverse apply itself fails / conflicts | a normal `failed`/`conflict` change — surfaced to the operator like any apply |
| Revert when `LIVE_PUSH_ENABLED` off | the inverse runs as a dry-run like every push (preview only), consistent with the master switch |

## Security

- Revert is `CONFIG_PUSH`-gated + CSRF + audited (`config.change.revert`), tenant+device scoped (the change is loaded under the tenant). The inverse goes through the same staleness guard (no clobber) and the same `LIVE_PUSH_ENABLED` master switch.
- The sweeper runs as owner but scopes nothing cross-tenant beyond what the original jobs already do (it only re-enqueues/marks by id); no new data exposure.
- Snapshot decryption for inverse building uses the existing `crypto` (Fernet) — the reconstructed payload is alias config, not secrets.

## Testing

- **Sweeper (pure classifier + job):** orphan (`scheduled`, overdue) → re-enqueue; orphan overdue past `orphan_max_age` **with no in-progress action on the device** → `failed`+alert; orphan overdue past max-age **but another action is in-progress on the device** → only re-enqueued, NOT given up; in-progress past timeout → `failed`+alert (no re-enqueue); `scheduled` within grace → untouched; both tables covered; per-row isolation (one error doesn't abort).
- **Heartbeat:** the firmware worker bumps `updated_at` via the side-channel session during its poll loop (a recently-heartbeated `running` row is not flagged stuck; a row whose heartbeat is older than `firmware_stuck_minutes` is).
- **Inverse builders:** alias `add`→`delete`; `delete`→`add` from snapshot; `set`→`set` previous from snapshot; missing-snapshot → error for `delete`/`set`; `has_inverse` for registered vs unregistered kinds.
- **Revert flow:** applied change → revert creates a linked inverse (`reverts_change_id`) → applies (mocked connector) → device state reverted; failed/partial source → idempotent convergence; RBAC (read_only denied), CSRF, tenant/device scoping; non-invertible kind → 400.
- **API + migration:** 0023 column exists + FK; revert endpoint happy path + guards.
- **Frontend:** Revert button visibility (state + revertible), confirm modal, list refresh + "reverts #…" link; `npm run build` green.

## Build phases (informs the plan; one cohesive milestone)

- **Phase A — Sweeper** (backend-only): settings + `action_sweeper.py` classifier (re-enqueue / give-up-with-device-gate / mark-stuck) + `sweep_stuck_actions` cron + alerts + the firmware-worker **heartbeat** + tests. Ships independently.
- **Phase B — Revert**: migration 0023 + `reverts_change_id`; `config_revert.py` (registry + alias inverse + snapshot helper) + `revert_change`; revert API; frontend button + list field; tests. Behind `LIVE_PUSH_ENABLED` like all pushes.

## Out of scope / future

- Inverse builders for the other kinds (`opnsense_setting`, `firewall_rule`, `monit_test`, `suricata_ruleset`) — the registry makes these incremental follow-ups; v1 disables the button for them.
- Firmware revert (no un-upgrade); full-config restore (no OPNsense API).

(These out-of-scope items are recorded as TODOs in project memory.)

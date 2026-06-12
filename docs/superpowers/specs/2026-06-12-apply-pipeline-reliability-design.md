# Apply-Pipeline Reliability — Design Spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Covers:** TODO items #1 (orphaned/stuck scheduled-action sweeper) and #2 (operator-triggered config-push revert) — designed together with the user as one cohesive "apply-pipeline reliability" milestone.

## Goal

Close two reliability gaps in the device-action apply pipeline:

1. **No auto-retry for lock-miss orphans.** Both `apply_config_change` and `run_firmware_action` take a per-device `pg_try_advisory_xact_lock`; on a lock miss they return the unchanged status **without raising**, so ARQ never retries and the row stays `scheduled` forever. A worker crash mid-op falls into the same bucket: the single end-of-job commit means a crash rolls the row back to `scheduled`. Both are handled by re-enqueuing genuine orphans.
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
3. **Sweeper handles orphaned `scheduled` rows only**, across **config_changes and firmware_actions**. There is **no committed "stuck in-progress" state** to handle: each worker job runs the whole apply (incl. the `applying`/`running` flushes) in **one transaction committed once at the end**, so a crash rolls back to `scheduled` — a crash IS an orphan. The genuine-orphan-vs-legitimately-running distinction is made with the **per-device advisory lock** (held for the duration of any real op): if the sweeper can acquire it, the device is free and the row is a true orphan; if not, a real op is running → skip.
4. **No wall-clock stuck timeouts and no heartbeat** (both were predicated on a committed in-progress state that does not exist). Give-up is **attempt-based**: a small `sweep_attempts` counter, incremented only when the sweeper re-enqueues a device-free orphan, so a long op ahead of an orphan never burns attempts (no false give-ups). The only time knobs are the sweep cadence and a short grace.
5. **Revert v1 implements `firewall_alias`** (the live-verified kind) behind an extensible `INVERSE_BUILDERS` registry; kinds without an inverse → the button is disabled with a reason. **Firmware revert is out of scope** (you cannot un-upgrade).

## Architecture

```
                ┌──────────────────────────────────────────────────────────────┐
  cron (5 min)  │ sweep_orphaned_actions (owner, RLS-exempt)                    │
  ─────────────▶│  for each overdue status='scheduled' row (both tables):       │
                │    try pg_try_advisory_xact_lock(device):                      │
                │      • NOT acquired (real op running) → skip                   │
                │      • acquired (device free) → genuine orphan:                │──▶ apply_config_change /
                │          sweep_attempts < MAX → ++attempts, re-enqueue ────────│    run_firmware_action
                │          else                  → mark 'failed' + alert         │
                └──────────────────────────────────────────────────────────────┘

  operator ──"Revert"──▶ POST /…/config/changes/{id}/revert
                          invert_change(change, snapshot) → inverse draft
                          (reverts_change_id = id) ──▶ create_change → schedule/apply
                          (same pipeline: preview → lock → snapshot → apply_for_kind → audit)
```

## Component 1 — Sweeper

**Why the design is orphan-only (transaction model).** Each worker job (`apply_config_change`, `run_firmware_action`) runs the whole operation in a single session and **commits once at the end**. The service's `applying`/`running` writes are *flushed but never committed* mid-op, and the per-device `pg_try_advisory_xact_lock` is held for the whole op. Therefore: (1) a crash mid-op rolls back to the last committed status — **`scheduled`** — so a crash is just an orphan; (2) during a legitimate long op the committed status is *also* `scheduled` (the `running` is uncommitted), so status alone can't tell them apart — but the **advisory lock is held** throughout, which can. There is no committed `applying`/`running` state to sweep.

**New worker cron `sweep_orphaned_actions(ctx)`** (`app/worker.py`), registered in `WorkerSettings.cron_jobs` at `minute=set(range(0, 60, sweep_every_minutes))` (default every 5 min). Runs as owner (RLS-exempt). A pure helper module **`app/services/action_sweeper.py`** holds the SQL-free classification (`decide_orphan(row, attempts, max_attempts) -> 're-enqueue' | 'give-up'`) so the policy is unit-testable without ARQ/DB.

Per table, the cron selects candidate rows `status='scheduled' AND COALESCE(scheduled_at, created_at) < now − ORPHAN_GRACE`. Each candidate is handled in **its own transaction** (one bad row never aborts the sweep):

1. `got = pg_try_advisory_xact_lock(_advisory_key(device_id))` (reuse `config_push._advisory_key`).
2. **If not acquired** → a real op is running on the device → **skip** (commit/rollback releases nothing held; move on). The row stays `scheduled`; a later sweep will re-check once the device is free.
3. **If acquired** (device free → genuine orphan):
   - `sweep_attempts < MAX_REENQUEUE_ATTEMPTS` → `sweep_attempts += 1`, **re-enqueue** the job (`apply_config_change`/`run_firmware_action`). Idempotent: the job re-checks `status=='scheduled'` and re-takes the lock.
   - else → **give up**: `status='failed'`, `result={"error":"orphaned: never applied after N re-enqueue attempts"}`, raise an alert.
   - Commit (releases the advisory lock immediately; the re-enqueued job — which runs later via ARQ — re-acquires it then).

- **Why attempt-based, not wall-clock, give-up:** an orphan correctly queued behind a long op is *skipped* (lock held) and never increments `sweep_attempts`, so its attempts only count device-free retries — a long op ahead of it can't cause a false give-up. With `MAX_REENQUEUE_ATTEMPTS=5` and a 5-min cadence, a genuinely unappliable orphan is failed after ~25 min of *free-device* retries.
- **No heartbeat, no stuck-timeout, no in-progress detection** — unnecessary given the transaction model above.
- Alerts reuse `Alert(tenant_id, device_id, type='action_orphaned', label=…)`; the cron returns `{re_enqueued, gave_up, skipped}` for logging.
- **New settings** (`app/core/config.py`): `sweep_every_minutes=5`, `orphan_grace_minutes=5`, `max_reenqueue_attempts=5`.
- **Schema:** add `sweep_attempts INTEGER NOT NULL DEFAULT 0` to **both** `config_changes` and `firmware_actions` (migration 0023, alongside `reverts_change_id`).

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

Migration **0023** (covers both phases):
- `config_change.reverts_change_id UUID NULL` FK→`config_changes(id)` `ON DELETE SET NULL` — links an inverse change to the one it reverts (traceability). No new status (reverts reuse the normal lifecycle).
- `config_change.sweep_attempts INTEGER NOT NULL DEFAULT 0` and `firmware_action.sweep_attempts INTEGER NOT NULL DEFAULT 0` — device-free re-enqueue counter for the sweeper's give-up.

### Frontend

In the device's **config-changes history** (existing component), add a **"Revert"** button on rows where `status ∈ {applied, failed}` and the kind is invertible. The list response gains `reverts_change_id` and a computed `revertible: bool` (kind has an inverse builder + state is applied/failed). Clicking calls the revert endpoint (now or with a schedule), then refreshes the list; the new inverse row shows a "reverts #…" link. Reuse the existing apply/preview confirm modal pattern. Mantine v9 + Midnight-NOC.

## Data flow summary

- **Sweeper:** cron → for each overdue `scheduled` row, try the device advisory lock → busy=skip / free=(re-enqueue if attempts left | give-up+alert), each in its own tx.
- **Revert:** button → revert endpoint → `build_inverse` → `create_change(reverts_change_id=…)` → schedule → `apply_config_change` (normal pipeline) → `applied`/`failed`/`conflict`.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Sweeper: a single row errors (DB/enqueue) | caught per-row, logged; the sweep continues with the next row |
| Sweeper: re-enqueued job lock-misses again | stays `scheduled`; re-tried next sweep (device-free) until `MAX_REENQUEUE_ATTEMPTS`, then given up + alert |
| Crashed worker mid-op | the uncommitted tx rolls back → row reverts to `scheduled` → handled as an orphan (advisory lock is free) |
| Orphan queued behind a legitimately long-running op | device advisory lock is held → the sweeper **skips** it (no attempt burned) until the op ahead finishes |
| Worker job committed `applied`/`failed`/`conflict` (terminal) | not a candidate (sweeper only selects `scheduled`) |
| Revert on a non-invertible kind / wrong state | 400/409 with a clear reason; button hidden/disabled |
| Revert of `delete`/`set` with no pre-apply snapshot | 409 "no pre-apply snapshot to reconstruct from" |
| Inverse apply itself fails / conflicts | a normal `failed`/`conflict` change — surfaced to the operator like any apply |
| Revert when `LIVE_PUSH_ENABLED` off | the inverse runs as a dry-run like every push (preview only), consistent with the master switch |

## Security

- Revert is `CONFIG_PUSH`-gated + CSRF + audited (`config.change.revert`), tenant+device scoped (the change is loaded under the tenant). The inverse goes through the same staleness guard (no clobber) and the same `LIVE_PUSH_ENABLED` master switch.
- The sweeper runs as owner but scopes nothing cross-tenant beyond what the original jobs already do (it only re-enqueues/marks by id); no new data exposure.
- Snapshot decryption for inverse building uses the existing `crypto` (Fernet) — the reconstructed payload is alias config, not secrets.

## Testing

- **Sweeper decision (pure `decide_orphan`):** attempts < max → `re-enqueue`; attempts ≥ max → `give-up`. No DB/lock needed for this unit.
- **Sweeper job (DB):** device lock free + attempts left → `sweep_attempts` incremented + job re-enqueued; device lock **held** (simulate by acquiring it in the test's own connection first) → row skipped, attempts unchanged; device free + attempts exhausted → `failed` + alert row; `scheduled` within grace → untouched; both `config_changes` and `firmware_actions` covered; a per-row error doesn't abort the sweep.
- **Inverse builders:** alias `add`→`delete`; `delete`→`add` from snapshot; `set`→`set` previous from snapshot; missing-snapshot → error for `delete`/`set`; `has_inverse` for registered vs unregistered kinds.
- **Revert flow:** applied change → revert creates a linked inverse (`reverts_change_id`) → applies (mocked connector) → device state reverted; failed/partial source → idempotent convergence; RBAC (read_only denied), CSRF, tenant/device scoping; non-invertible kind → 400.
- **API + migration:** 0023 column exists + FK; revert endpoint happy path + guards.
- **Frontend:** Revert button visibility (state + revertible), confirm modal, list refresh + "reverts #…" link; `npm run build` green.

## Build phases (informs the plan; one cohesive milestone)

- **Phase A — Sweeper** (backend-only): the `sweep_attempts` columns (migration 0023 can land here or in B — see plan) + settings + `action_sweeper.decide_orphan` + the `sweep_orphaned_actions` cron (advisory-lock gate) + alerts + tests. Ships independently.
- **Phase B — Revert**: migration 0023 + `reverts_change_id`; `config_revert.py` (registry + alias inverse + snapshot helper) + `revert_change`; revert API; frontend button + list field; tests. Behind `LIVE_PUSH_ENABLED` like all pushes.

## Out of scope / future

- Inverse builders for the other kinds (`opnsense_setting`, `firewall_rule`, `monit_test`, `suricata_ruleset`) — the registry makes these incremental follow-ups; v1 disables the button for them.
- Firmware revert (no un-upgrade); full-config restore (no OPNsense API).

(These out-of-scope items are recorded as TODOs in project memory.)

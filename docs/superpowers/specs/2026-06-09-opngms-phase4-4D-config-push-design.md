# OPNGMS — Phase 4 / Milestone 4D-a: Config Change & Push Pipeline — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user authorized proceeding
- **Phase:** 4 of 5 — Milestone 4D-a (the push pipeline; first mutating phase)
- **Depends on:** 4A (`config_snapshots`, `canonical_hash`, encrypted), 4B (config model), worker/ARQ, crypto, AuditService, RLS — all in `main`
- **Enables:** 4D-b (real OPNsense push), 4D-c (editing UI), 4D-d (more section types)

---

## 1. Context

**4D** is the first phase that **mutates** firewall configuration. 4D is decomposed:
- **4D-a** *(this spec)*: the **change & push pipeline** — propose → preview → schedule
  (immediate/deferred) → staleness-guarded apply → audit — **granular per-section**, with **firewall
  aliases** as the first section type, and the actual mutation **abstracted behind a dry-run connector**
  (the real OPNsense endpoints are unverified and no real device is wired yet).
- **4D-b**: real OPNsense push integration (verify endpoints against a real device, flip dry-run off).
- **4D-c**: the editing UI (write-only sensitive fields, preview, schedule picker).
- **4D-d**: broaden the editable surface beyond aliases.

4D-a builds the **dangerous machinery** safely: the full pipeline is real and tested, but no firewall
is actually changed (dry-run default) until 4D-b validates against a real device.

## 2. Design decisions (4D brainstorming)

| Topic | Decision |
|-------|----------|
| Push granularity | **Granular per-section via OPNsense plugin APIs** (low blast radius, no reboot); start with **aliases** |
| Apply now | **Pipeline built now, apply abstracted/dry-run** (mutation behind a `dry_run`-default connector, mocked respx); real push is 4D-b |
| Staleness | **Re-check `canonical_hash` (4A) at apply time** (incl. deferred); mismatch → `conflict`, no clobber |
| Scheduling | **Immediate or deferred** (operator-picked date/time) via ARQ deferred jobs |
| Authorization | New RBAC action **`CONFIG_PUSH`** (elevated; mutates firewalls) granted to `tenant_admin` + `operator` |
| Concurrency | **Per-device serialization** (one apply at a time per firewall) via a Postgres advisory lock |
| Secrets | **Write-only** payload hook (sensitive fields encrypted, "leave blank to keep") — not exercised by aliases, ready for secret-bearing sections later |

## 3. Data model — `config_changes` (relational, tenant-scoped, RLS)

```
config_changes(
  id            UUID PK,
  tenant_id     UUID NOT NULL,                 -- RLS
  device_id     UUID NOT NULL FK devices ON DELETE CASCADE,
  created_by    UUID NOT NULL,                 -- user who proposed it (audit)
  kind          TEXT NOT NULL,                 -- section type, e.g. 'alias'
  operation     TEXT NOT NULL,                 -- 'add' | 'set' | 'delete'
  target        TEXT NOT NULL DEFAULT '',      -- e.g. alias name
  payload       JSONB NOT NULL DEFAULT '{}',   -- the new values (write-only secrets handled here later)
  baseline_hash TEXT NOT NULL,                 -- canonical_hash of the config it was built against
  status        TEXT NOT NULL DEFAULT 'draft', -- draft|scheduled|applying|applied|failed|conflict|cancelled
  scheduled_at  TIMESTAMPTZ,                   -- NULL = immediate (set when scheduled/applied)
  applied_at    TIMESTAMPTZ,
  result        JSONB NOT NULL DEFAULT '{}',   -- dry-run preview / apply outcome (secret-safe)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
```
- RLS keyed on `tenant_id` (added to `TENANT_TABLES`), like the other tenant tables. Index on
  `(tenant_id, device_id, created_at DESC)`.
- The worker (owner) writes status/result; the API reads/writes as `opngms_app` under RLS.

## 4. Status lifecycle

`draft` → (`schedule`) → `scheduled` → (apply job runs) → `applying` → `applied` | `failed` | `conflict`.
`draft`/`scheduled` → (`cancel`) → `cancelled`. The **staleness guard** can move `applying` → `conflict`.

## 5. Pipeline

1. **Propose (`POST /config/changes`)**: create a `draft` for a device with `kind=alias`,
   `operation`, `target`, `payload`, and `baseline_hash` = the latest snapshot's `canonical_hash`
   (the config the operator is editing against). Audited (`config.change.create`).
2. **Preview (`GET /config/changes/{id}/preview`)**: a **dry-run, secret-safe** description of what
   the change would do — the operation + target + the new payload, and (if the alias exists in the
   latest snapshot) its current value for comparison. No firewall contact. No secret values emitted.
3. **Schedule/apply (`POST /config/changes/{id}/schedule`)**: body `{ scheduled_at?: datetime }`.
   - Immediate (no `scheduled_at`) → enqueue `apply_config_change(id)` now; status → `scheduled`.
   - Deferred → enqueue with ARQ `_defer_until=scheduled_at`; status → `scheduled`, `scheduled_at` set.
   Audited (`config.change.schedule`). Requires **`CONFIG_PUSH`**.
4. **Apply job (`apply_config_change(change_id)`)** (worker, owner):
   - Load the change; if not `scheduled` (e.g. cancelled), no-op.
   - **Per-device serialization**: take a Postgres **advisory lock** keyed on `device_id` (only one
     apply per firewall at a time).
   - **Staleness guard**: build an `OpnsenseClient`, fetch the current config, compute
     `canonical_hash`; if it **differs** from `baseline_hash` → status `conflict`, store a note in
     `result`, audit (`config.change.conflict`), release lock, **do not apply** (no clobber).
   - Else: status `applying` → call the connector apply (`apply_alias(..., dry_run=True)`); on success
     status `applied`, store the (secret-safe) result, `applied_at`; on connector error status
     `failed`. Audit (`config.change.apply`). Then enqueue a fresh `backup_device_config` so the new
     snapshot reflects the change. Release the lock.
5. **Cancel (`POST /config/changes/{id}/cancel`)**: `draft`/`scheduled` → `cancelled`. Audited.

## 6. Connector extension

- `apply_alias(operation: str, payload: dict, *, dry_run: bool = True) -> dict` → the OPNsense firewall
  alias API (`/api/firewall/alias/addItem|setItem|delItem` + `/api/firewall/alias/reconfigure`).
  **`dry_run=True` (default)** performs NO mutation and returns a `{"dry_run": true, ...}` stub; the
  real call path is wired but gated until 4D-b. ⚠️ **Endpoints TO VERIFY** against a real device;
  mocked with respx. Goes through the single SSRF-guarded HTTP boundary (`_get`/a new `_post` if a
  body is needed — add a guarded POST mirroring `_request`).

## 7. Security & safety

- **Mutation is gated**: `dry_run=True` by default (no real change in 4D-a); the apply job passes
  `dry_run=True`. A single flag flip (4D-b) enables real pushes once verified.
- **Staleness guard** prevents clobbering intervening changes (re-check hash at apply, incl. deferred).
- **Preview-before-apply** + explicit schedule action.
- **RBAC `CONFIG_PUSH`** (tenant_admin + operator) on schedule/apply; create/preview need `DEVICE_VIEW`
  (or `CONFIG_PUSH`). **All audited** via AuditService.
- **Per-device serialization** (advisory lock) — no concurrent applies to one firewall.
- **Tenant isolation**: RLS on `config_changes` + tenant filter; cross-tenant isolation test (real
  `opngms_app`).
- **Write-only secrets** (design hook): sensitive payload fields would be encrypted at rest and use
  "leave blank to keep" on apply; aliases carry no secrets, so 4D-a stores plain payload but the
  service is structured to add encryption for secret-bearing kinds later. No secret is ever returned.

## 8. Worker

- `apply_config_change` registered in `WorkerSettings.functions`. Immediate pushes enqueue it now;
  deferred pushes enqueue with `_defer_until`. No new cron (apply is event-driven, not scheduled).

## 9. Milestone 4D-a breakdown (for the plan)
1. **`config_changes` model + migration + RLS** + **`CONFIG_PUSH`** RBAC action.
2. **Connector `apply_alias`** (dry-run default, DA VERIFICARE) + a guarded `_post` if needed + respx test.
3. **Change service**: create (draft + baseline_hash) + preview (dry-run, secret-safe) + tests.
4. **Apply job + worker wiring**: staleness guard + advisory-lock serialization + dry-run apply +
   audit + backup-refresh enqueue; immediate + deferred (ARQ `_defer_until`) + tests.
5. **API**: create / preview / schedule / list / cancel — tenant-scoped + RLS + `CONFIG_PUSH` + isolation tests.

## 10. Definition of "Done" (4D-a)
- An operator can propose an alias change, preview it (secret-safe), and schedule it immediately or for
  a future date/time.
- The apply job re-checks `canonical_hash` (staleness guard) — a changed config yields `conflict`, not
  a clobber — serializes per device, runs the **dry-run** apply (no real mutation), audits every step,
  and refreshes the snapshot.
- Everything is tenant-scoped + RLS-isolated and gated by `CONFIG_PUSH`; proven by tests.
- Suite green + `alembic check` clean.

## 11. Non-goals (4D-a) / deferred
- **Real firewall mutation** (4D-b — flip `dry_run` off after verifying endpoints against a real device).
- **Editing UI** (4D-c) and **section types beyond aliases** (4D-d).
- **Full-config restore push** (not chosen — granular per-section instead).
- **Rollback/undo** of an applied change (a later safety feature; the snapshot history gives the prior state).

## 12. Open questions (non-blocking)
- **OPNsense alias API** exact endpoints/payload (`addItem`/`setItem`/`delItem`/`reconfigure`) — verify
  against a real device (4D-b); mocked until then.
- **Preview fidelity**: 4D-a previews locally (operation + payload + current-from-snapshot). A real
  device-side validation/dry-run (if the alias API supports it) can enrich it in 4D-b.
- **Advisory-lock key**: derive a stable bigint from `device_id` (e.g. hashtext) for
  `pg_try_advisory_xact_lock`; confirm the chosen hashing.
- **CONFIG_PUSH grant**: tenant_admin + operator (proposed); adjust if pushes should be tenant_admin-only.

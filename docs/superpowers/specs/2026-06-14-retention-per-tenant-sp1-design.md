# Per-tenant retention — Sub-project 1 (Postgres-side stores) — Design

**Date:** 2026-06-14
**Status:** Approved (design); ready for implementation plan.
**Branch:** `feat/retention-per-tenant`
**Milestone:** "Retention settings" — make the retention of the data behind dashboards & reports operator-
configurable, with a **global default + per-tenant override** (each MSP client controls how long its data is
kept in OPNGMS).

## Scope of this sub-project (SP-1)

The milestone covers four data stores. SP-1 delivers the **foundation** + per-tenant retention for the three
**Postgres-side** stores; the fourth (the OpenSearch log lake) is **SP-2**, a separate spec→plan→build that
reuses this foundation.

| Store | Feeds | Retention today | In SP-1? |
|-------|-------|-----------------|----------|
| `perimeter_attacker` (firewall blocks + failed logins rollup) | Overview cards, report sections | constant `RETENTION_DAYS=30` | ✅ |
| `events` (IDS/DNS, TimescaleDB hypertable) | attacker-countries, IDS/DNS panels, reports | native `add_retention_policy('events', 90d)` | ✅ |
| `metrics` (device-health telemetry, TimescaleDB hypertable) | Health tab, report device-health | native `add_retention_policy('metrics', 30d)` | ✅ |
| log lake (syslog → OpenSearch) | Log fleet dashboard | OpenSearch ISM by daily index, env `log_retention_days=30` | ⛔ SP-2 |

## Goals

1. A **global default** retention (days) for each SP-1 store, editable by a superadmin (env default + DB
   override, via the existing runtime-settings registry).
2. A **per-tenant override** for each, editable from the tenant's page; absent → inherit the global default.
3. Enforcement: each store is purged per tenant at its **effective** retention, replacing the native
   global TimescaleDB chunk-drop policies (which cannot be per-tenant).
4. Defaults preserve today's behavior (perimeter 30, events 90, metrics 30).

## Non-goals

- **The log lake** (SP-2): per-tenant OpenSearch indices + per-tenant index retention. SP-1 does not touch
  the syslog pipeline or `log_retention_days`.
- No change to **what** the dashboards/reports query — only how long the data lives.
- No UI to browse/restore purged data. Purge is destructive and final (it always was).

## Design decisions (user-approved 2026-06-14)

- **Full per-tenant override** on every store (not global-only).
- The override lives on the **tenant's page** (the operator/tenant admin who manages that client sets it).
- Disk caveat (below) **accepted**.

## Architecture

### Data model — mirror the report-sections pattern (`BUILTIN < tenant`)

**Global defaults** — three new entries in the runtime-settings registry
(`app/services/runtime_settings.py`), with matching `Settings` (env) defaults in `app/core/config.py`:

| key | default (env) | bounds | group |
|-----|---------------|--------|-------|
| `perimeter_retention_days` | 30 | 1 – 3650 | `retention` |
| `events_retention_days` | 90 | 1 – 3650 | `retention` |
| `metrics_retention_days` | 30 | 1 – 3650 | `retention` |

These are read at use-time by the purge jobs (the registry's existing pattern: env default + a single DB
override row, no restart needed). They are the **fallback** for any tenant that has not overridden.

**Per-tenant overrides** — a new RLS-scoped table `tenant_retention`, mirroring `report_settings`:

```
tenant_retention(
  tenant_id   UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
  overrides   JSONB NOT NULL DEFAULT '{}',   -- partial: {"perimeter": N, "events": N, "metrics": N}
  updated_at  TIMESTAMPTZ
)
```

A JSONB partial map (not fixed columns) so SP-2 can add `"log_lake"` without a schema change. An absent key
= inherit the global default. Added to `app/core/rls.py TENANT_TABLES` (fail-closed RLS policy, like every
tenant table).

**Resolver** — a pure function, mirroring `resolve_sections`:

```python
def effective_retention_days(store: str, *, global_default: int, tenant_override: dict | None) -> int:
    v = (tenant_override or {}).get(store)
    return int(v) if isinstance(v, int) and v > 0 else global_default
```

Precedence: **global default < per-tenant override**. A corrupt/out-of-range override is ignored (the
default stands) — same defensive stance as `get_runtime_config`.

### Enforcement — tenant-aware purge jobs (worker, owner connection)

The worker connects as the DB **owner** (RLS-exempt, the only role that may drop TimescaleDB retention
policies and that can see all tenants). All purges run there, on a daily cron.

- **perimeter** — generalize `purge_perimeter`: load the global default + every `tenant_retention` row, and
  for each tenant present in the rollup, `DELETE FROM perimeter_attacker WHERE tenant_id = :t AND last_seen
  < now - effective_days(:t)`. (One statement per distinct tenant, or a single statement with a
  `VALUES`/join of (tenant_id, cutoff) pairs.)
- **events** — a new `purge_events` job: `DELETE FROM events WHERE tenant_id = :t AND time < now -
  effective_days(:t)` per tenant.
- **metrics** — a new `purge_metrics` job: same shape on `metrics.time`.
- **Remove the native TimescaleDB retention policies** (migration 0038): `SELECT remove_retention_policy(
  'events', if_exists => true)` and `…('metrics', …)`. After this, OPNGMS owns retention entirely; nothing
  drops `events`/`metrics` except our jobs.

A single daily cron entry can drive all three (one job that purges perimeter + events + metrics), or three
small jobs — the plan decides. Each purge is **best-effort and independent**: one store failing must not
block the others (wrap each in its own try/except, log + continue), mirroring the perimeter-ingest SAVEPOINT
guard.

**Disk caveat (accepted, documented in code).** On a TimescaleDB hypertable, a chunk's disk is reclaimed
only when the **whole chunk** is dropped, which now happens only once it is empty — i.e. once the
**longest-retention tenant's** rows in that time-window have aged out. So per-tenant row-deletes make a
short-retention tenant's data **non-queryable** at its own cutoff, but disk is reclaimed at the pace of the
longest retainer. This is governance/privacy by design; the disk profile is roughly "max tenant retention,"
not "min." (A future safety valve — a global hard ceiling that chunk-drops regardless — is out of scope for
SP-1; noted in SP-2/follow-up if disk pressure appears.)

### API

- **Global defaults** — no new endpoint. The three keys ride the existing
  `GET`/`PUT /api/admin/settings` (superadmin, CSRF, audited). Per the registry's `active` rule (never expose
  a knob whose consumer isn't wired), each key is flipped `active=True` in the **same PR that wires its
  purge consumer**: `perimeter_retention_days` active in PR1; `events_retention_days` /
  `metrics_retention_days` added `active=False` in PR1 and flipped to `active=True` in PR2. The same staging
  applies to the per-tenant override UI/keys (events/metrics fields appear once their purge is live).
- **Per-tenant override** — a new tenant-scoped pair on the `/api/tenants/{tenant_id}` surface
  (`app/api/retention.py` or folded into an existing tenant router):
  - `GET /api/tenants/{tenant_id}/retention` → `{ overrides: {...}, defaults: {perimeter, events, metrics} }`
    so the UI can render "inherit (N)" hints.
  - `PUT /api/tenants/{tenant_id}/retention` → upsert the partial overrides (validate keys ∈
    {perimeter, events, metrics}, each int within bounds; a `null` clears an override back to inherit).
  - Gated by a new tenant-level action **`Action.RETENTION_CONFIG`** granted to `tenant_admin` (the client's
    admin manages their own retention); superadmin always allowed. CSRF on the PUT; **audited**
    (`tenant.retention.update`). RLS scopes the read/write to the caller's tenant.

### UI

- **Global** — the runtime-settings section is generic (`RuntimeSettingsSection.tsx` renders by group); add
  `"retention"` to `GROUP_ORDER` and the group + per-item i18n labels. The three knobs then appear under
  System → Runtime settings automatically.
- **Per-tenant** — a **"Retention" card on the tenant's settings page** (mirror `ReportSettingsPage.tsx`,
  which is already per-active-tenant): three number inputs, each showing the inherited global as the
  placeholder/help ("Inherit global: N"), an explicit override value, and a way to clear back to inherit.
  Visible to `tenant_admin`/superadmin.
- **i18n** across all 12 locales (group label, three item labels+help, the per-tenant card strings, an
  `errors.retentionLoad` key). `tsc -b` enforces parity.

## Invariants / security

- `tenant_retention` is RLS-scoped (in `TENANT_TABLES`); the per-tenant API runs as `opngms_app` and only
  ever touches the caller's tenant row. The **purge jobs** run as the owner in the worker — never on a
  user-facing path — consistent with the RLS invariant ("never run user-facing queries as the owner; the
  worker is trusted infra").
- Bounds (1–3650) on every value, global and per-tenant, guard a typo from wedging a purge.
- No secrets involved. Audit the per-tenant PUT and (already) the global PUT.

## Testing

- **Resolver:** unit tests — override wins; absent/zero/out-of-range/corrupt override → global default.
- **Per-tenant purge:** seed two tenants with rows at varied ages + different effective retentions (one
  global-default, one overridden shorter/longer); run each purge; assert each tenant's rows are cut at ITS
  cutoff and the other tenant is untouched. One test per store (perimeter, events, metrics).
- **Migration 0038:** the native policies are gone afterward (`SELECT … FROM timescaledb_information.jobs`
  shows no retention job for events/metrics); fresh-DB `upgrade head` still works.
- **API:** authz (non-`tenant_admin` → 403 on PUT; cross-tenant isolation via RLS — tenant A cannot read/write
  tenant B's overrides); validation (unknown key / out-of-range → 422); GET returns defaults + overrides.
- **Frontend:** the global group renders the three knobs; the per-tenant card shows inherit hints, saves an
  override, clears it. Build gate `npm run build` + `npm test` + `npm run lint`.

## Report ↔ retention consistency (user 2026-06-14)

A report must never be configured to cover more days than the data it needs is retained — otherwise it would
request already-purged data. Reports draw from `events`/`metrics`/`perimeter` (NOT the log lake), so this is
fully an SP-1 concern. See [[report-retention-consistency-rule]] (memory) for the authoritative rule.

**The bound.** For a given report, the bound = the **minimum** effective retention across the stores its
**enabled sections** use (`effective = override ?? global`, the SP-1 resolver). Section → store map (only the
three retention-bounded stores; sections backed by alerts/config-changes contribute no bound):

| sections | store |
|----------|-------|
| `failed_logins`, `firewall_blocks` | perimeter |
| `attacks`, `attacker_countries`, `applications`, `web_filter` | events |
| `health`, `web`, `data`, `status` | metrics |
| `summary` | events + metrics |
| `alerts_wan` | metrics |
| `firmware_config` | (none) |

(The implementer must verify each mapping against the aggregator each section calls.) Report range sources:
on-demand `POST /reports` = explicit `from`/`to`; scheduled = `report_window(frequency)` — weekly/on-demand =
prior 7 days, monthly = prior calendar month (treat as **31** for the check, the max month length).

**Enforcement — ASYMMETRIC. NO CLAMP** (user: "non deve poter succedere il clamp nei report"):
- **Report side = BLOCK.** Reject configuring a report whose range > current bound.
  - `POST /reports` (on-demand): 400 if `(to-from).days` > bound (bound from the tenant's enabled report
    settings sections). Sits alongside the existing `MAX_RANGE_DAYS=92` check in `reporting/service.py`.
  - `PUT /report-schedules`: 422 if `report_window(frequency).days` > bound (bound from
    `resolve_sections(tenant_settings, schedule.sections)`). E.g. can't pick `monthly` if the bound < 31.
- **Retention side = WARN, do NOT block.** Lowering retention is allowed; the system surfaces the conflict:
  - `GET /api/tenants/{id}/retention` returns a **`warnings`** list (each enabled schedule whose range now
    exceeds its bound: `{schedule_id, frequency, range_days, bound}`) — **computed on read**, so the tenant's
    Retention card always shows the current truth regardless of how the drift arose.
  - the **global** `PUT /api/admin/settings` (superadmin), when a retention key is lowered, returns the
    **list of impacted tenants** (no override for that store + an enabled schedule using that store whose
    range now exceeds the new global) as immediate feedback.
- **NO generation-time clamp.** A report keeps its configured range; purged days render as "no data in this
  period" (existing empty-period handling). Consistency is held by the block + the warning, never by silently
  shortening the window.

**UI:** the tenant's Retention card / report settings surface shows the `warnings` (e.g. "Report X (monthly)
needs 31 days but events are kept N days"). The global System settings page shows the impacted-tenants list
returned by the PUT. i18n across 12 locales.

## Decomposition (PRs within SP-1)

1. **PR1 — Foundation + perimeter.** Registry keys (3) + `Settings` fields + `tenant_retention`
   model/migration 0038 (table + RLS) + resolver + the per-tenant API (`RETENTION_CONFIG` action, GET/PUT,
   audited) + tenant-aware `purge_perimeter`. Backend + tests. **(MERGED #157)**
2. **PR2 — events + metrics.** Migration: `remove_retention_policy` for both. New `purge_events` /
   `purge_metrics` tenant-aware jobs + worker cron wiring + tests. Backend. **(MERGED #158)**
3. **PR3 — Frontend.** Global "retention" group (GROUP_ORDER + i18n) + per-tenant Retention card on the
   tenant page + i18n (12 locales) + `gen:api` + tests. **(MERGED #159)**
4. **PR4a — Report-side BLOCK (backend).** Section→store map + the `report_range_bound(tenant, sections)`
   helper (over the resolver) + the block on `POST /reports` + `PUT /report-schedules` + tests.
5. **PR4b — Retention-side WARN (backend + frontend).** `GET /retention` `warnings` (compute-on-read) +
   global settings PUT impacted-tenants list + surface both in the UI (tenant Retention card + System page) +
   i18n + tests.
6. **Release** — tag **v0.11.0** + CHANGELOG (SP-1). (SP-2 — log lake per-tenant — will be a later minor.)

## Risks / open items

- **Native-policy removal is one-way in practice** (migrations are forward-only). After 0038, retention is
  entirely ours; the new purge jobs must be in the same release so events/metrics never grow unbounded in
  the window between policy-removal and job-wiring. → keep PR2 atomic (remove + replace together) and verify
  on a fresh DB.
- **Disk reclaim** follows the longest tenant retention (caveat above) — acceptable for SP-1; revisit a
  global ceiling only if disk pressure is observed.
- **Per-tenant purge cost** is row-level DELETE on hypertables (vs chunk-drop). Fine at current volumes;
  if it becomes hot, batch by chunk/time-window. Noted, not optimized now (measure first).

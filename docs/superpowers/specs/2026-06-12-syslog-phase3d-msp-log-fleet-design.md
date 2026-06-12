# Syslog Phase 3.4 — MSP cross-tenant log-fleet dashboard (design spec)

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 3**, sub-project **3.4 of 4** — the LAST Phase-3 sub-project
(3.1 #67, 3.2 #68, 3.3 #69 merged; **3.4 — this spec**).

## Goal

A **superadmin-only** cross-tenant "Log fleet" dashboard: at a glance, which tenants/devices are
forwarding logs, whether logs are actually arriving (ingest health), and how much volume each tenant
produces. This is the **first true cross-tenant aggregate** in the console — every prior view is
tenant-scoped (RLS, or the path-injected tenant filter). It is therefore gated to the superadmin and
returns **aggregates only — never raw cross-tenant log content**.

## Locked decisions (from brainstorming)

- **Three metrics:** forwarding status (per-tenant enabled/disabled/revoked + device totals), ingest
  health (per-tenant last-log + a "silent" flag when enabled devices produce no recent logs), log
  volume (per-tenant 24h doc count).
- **Cross-tenant access:**
  - **Relational (forwarding status)** — a per-tenant loop: list tenants (the `tenants` table is NOT
    RLS-scoped), then for each tenant `set_tenant_context` + COUNT `device_log_forwarding`. Respects
    RLS (no bypass role); N short COUNT queries in one transaction.
  - **Log-derived (volume + last-log)** — an OpenSearch `terms` aggregation on `tenant_id` **without a
    tenant filter** (the only no-filter path; strictly superadmin-gated; returns counts/timestamps
    only). Best-effort: OpenSearch down → those columns are null, the relational part still renders.
- **Ingest health is tenant-level** (not per-device); **24h fixed volume window**. Per-device
  drill-down, window selector, export, and silent-tenant alerting are out of scope.

## What exists (to reuse)

- RBAC `app/core/rbac.py`: `Action` enum, `_ORG_ACTIONS` (superadmin-only set), `can(*, is_superadmin,
  role, action)`; `require_org(action)` in `app/core/deps.py` (returns the `User`, sets **no** tenant
  context).
- `TenantRepository(session).list() -> list[Tenant]` (`Tenant{id, name, slug, status, note}`).
- `set_tenant_context(session, tenant_id)` (`app/core/db.py`) — sets `app.current_tenant` for RLS.
- `DeviceLogForwarding{tenant_id, enabled, revoked_at, …}` (3.1/3.2); `Device{tenant_id, …}`.
- Phase-2/3 OpenSearch client `app/services/log_search.py` (httpx pattern, `LogSearchError`).
- Frontend superadmin pattern: `me?.is_superadmin && <NavItem to="/admin/…">`, routes under
  `/admin/…` (e.g. `/admin/templates`, `/admin/smtp`) in `frontend/src/components/AppShell.tsx`.

## Components

### 1. RBAC — `app/core/rbac.py`

Add `LOG_FLEET_VIEW = "log_fleet.view"` to `Action` (org-level section) and to `_ORG_ACTIONS`. Org
actions are superadmin-only by construction (`can` returns True for superadmin, False otherwise) —
no `_TENANT_MATRIX` entry.

### 2. Service — `app/services/log_fleet.py`

- **`async def fleet_forwarding_counts(session) -> dict[uuid.UUID, dict]`** — `TenantRepository(session).list()`,
  then for each tenant: `set_tenant_context(session, tid)` and run two grouped counts —
  `device_log_forwarding` rows split into `enabled` (`enabled = true`), `revoked` (`enabled = false AND
  revoked_at IS NOT NULL`), `disabled` (the rest with a row), and `total_devices` (`devices` count).
  Returns `{tenant_id: {enabled, disabled, revoked, total_devices}}`. (One transaction; the local
  `set_config` is re-applied per tenant.)
- **`async def fleet_log_stats(settings) -> dict[str, dict]`** — one OpenSearch `_search` (`size: 0`)
  with `aggs`: a `terms` agg on `tenant_id` (size = a generous cap, e.g. 1000) with two sub-aggs:
  `max @timestamp` (`last_log_at`) and a `filter` sub-agg (`range @timestamp gte now-24h`) →
  `volume_24h` (its `doc_count`). **No tenant filter.** Returns `{tenant_id_str: {last_log_at,
  volume_24h}}`. Wrapped in `try/except` → `{}` on any OpenSearch error (best-effort).
- **`async def log_fleet_overview(session, settings) -> dict`** — combines the two into per-tenant rows
  (`tenant_id, tenant_name, enabled, disabled, revoked, total_devices, last_log_at, volume_24h`) plus
  `totals` (`tenants_with_forwarding`, `enabled_devices`, `volume_24h` summed, `silent_tenants` =
  tenants with `enabled > 0` and (`last_log_at` is null OR older than a staleness threshold, e.g. 1h)).

### 3. API — `app/api/log_fleet.py`

`GET /api/admin/log-fleet` with `Depends(require_org(Action.LOG_FLEET_VIEW))` (a read — no CSRF):
calls `log_fleet_overview(session, get_settings())`, returns `LogFleetOut`. Mounted in `app/main.py`.

### 4. Schemas — `app/schemas/log_fleet.py`

```python
class LogFleetRow(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    enabled: int
    disabled: int
    revoked: int
    total_devices: int
    last_log_at: datetime | None
    volume_24h: int | None

class LogFleetTotals(BaseModel):
    tenants_with_forwarding: int
    enabled_devices: int
    volume_24h: int
    silent_tenants: int

class LogFleetOut(BaseModel):
    tenants: list[LogFleetRow]
    totals: LogFleetTotals
```

### 5. Frontend — `frontend/src/pages/LogFleetPage.tsx` + hook + nav/route

- `frontend/src/logs/logFleetHooks.ts` — `useLogFleet()` (`useQuery` GET, typed from the client).
- `LogFleetPage.tsx` — summary cards (tenants forwarding, enabled devices, 24h volume, **silent
  tenants**) + a per-tenant `Table` (Tenant | Forwarding | Revoked | Last log | Volume 24h) with a
  **"silent"** badge on rows where `enabled > 0` and `last_log_at` is null/stale. Loading + empty +
  error states. When OpenSearch is down (`last_log_at`/`volume_24h` null fleet-wide) the table shows
  "—" / "ingest unknown" rather than failing.
- `AppShell.tsx` — a `me?.is_superadmin && <NavItem to="/admin/log-fleet">` nav item + a
  `<Route path="/admin/log-fleet" element={<LogFleetPage />} />`; an i18n `nav.logFleet` label.

## Data flow

Superadmin opens `/admin/log-fleet` → `GET /api/admin/log-fleet` → `require_org(LOG_FLEET_VIEW)` (403
otherwise) → `fleet_forwarding_counts` (per-tenant RLS loop) + `fleet_log_stats` (one no-filter
OpenSearch agg) → combined rows + totals → cards + table.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Non-superadmin caller | 403 (`require_org`) |
| OpenSearch unreachable | `fleet_log_stats` → `{}`; `last_log_at`/`volume_24h` null; relational columns still render; "ingest unknown" |
| A tenant with no devices / no forwarding | row with zero counts (or omitted from the table if it has no `device_log_forwarding` rows — include all tenants for completeness) |
| No tenants | empty table + zeroed totals |

## Security

- **The ONLY no-tenant-filter query is the OpenSearch fleet aggregation**, strictly `LOG_FLEET_VIEW`
  (superadmin) gated, and it returns **only aggregates** (per-tenant counts + a max timestamp) — never
  document `_source`/log content. The relational loop sets the RLS context per tenant (no bypass role,
  no BYPASSRLS).
- No secrets in the response (counts/timestamps/names only). Read-only endpoint, no state change.
- This is a deliberate, audited expansion of cross-tenant visibility limited to the superadmin; it does
  not weaken any tenant-scoped path (the per-tenant log search still injects the path tenant filter).

## Testing

- **Service (DB + respx):** `fleet_forwarding_counts` over 2 seeded tenants returns correct
  enabled/disabled/revoked/total per tenant (and does not leak one tenant's count into another —
  the loop sets context correctly); `fleet_log_stats` maps an OpenSearch terms-agg response to
  per-tenant `{last_log_at, volume_24h}` and returns `{}` on a 5xx; `log_fleet_overview` combines them
  and computes `silent_tenants` (enabled>0 + stale/missing last_log).
- **API:** superadmin → 200 with rows+totals (OpenSearch mocked); a tenant_admin/operator/read_only →
  403; the combined shape asserted.
- **Frontend (vitest + MSW):** the page renders the cards + per-tenant rows, shows the "silent" badge
  for a stale tenant, and renders with null ingest columns when OpenSearch data is absent; a
  non-superadmin does not see the nav item (or sees a forbidden state if routed directly). `npm run
  build` green.

## Out of scope (tracked in the deferred backlog)

- Per-device cross-tenant silent-device drill-down; a volume time-window selector (24h/7d/30d);
  fleet-table export (CSV/PDF); proactive alerting on silent tenants; a single-query BYPASSRLS
  optimization of the relational loop (only if the tenant count grows large).

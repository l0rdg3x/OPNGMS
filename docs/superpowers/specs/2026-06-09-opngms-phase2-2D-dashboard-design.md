# OPNGMS — Phase 2 / Milestone 2D: Frontend Dashboard — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user has delegated decisions and authorized to proceed
- **Phase:** 2 of 5 (Milestone 2D, last of Phase 2)
- **Depends on:** Milestone D (frontend shell + auth + device) and Milestone 2C (metrics/health/alert API) in `main`

---

## 1. Context

**2D** closes Phase 2 by giving the console a **monitoring dashboard**: it consumes the three
2C endpoints (`GET .../devices/{id}/metrics`, `GET .../health`, `GET .../alerts`) and presents them as
charts over time, fleet health summaries, and a manageable alert list. Builds on the patterns
already established by Milestone D: Vite + React 19 + Mantine v9 + React Router + TanStack Query,
typed API client (`openapi-fetch` + generated `schema.d.ts`), Vitest + RTL + MSW tests.

## 2. Design Decisions (2D brainstorming)

| Topic | Decision |
|-------|----------|
| Charts library | **Mantine Charts** (`@mantine/charts`, on Recharts): integrated with the Mantine theme already in use, minimal API (`LineChart`/`AreaChart`/`DonutChart`). Zero friction with the stack |
| MVP scope | **Complete + alert management**: per-device health view, per-client overview, alert page with active/historical filter |
| Per-device metrics | **Essential + network**: CPU/mem/disk (series) + interface traffic + gateway status (RTT/loss/up) and VPN (up) + last_seen/firmware |
| Overview placement | **`OverviewPage` as tenant landing** (`/`): health cards are the first thing an MSP wants to see |

## 3. Architecture

### 3.1 Routing & navigation (reorganizes `AppShell`)
Today `AppShell` has `/` = `DevicesPage`. 2D reorganizes the routes (inside `MantineAppShell.Main`)
and adds navbar entries:

| Route | Page | Navbar |
|-------|------|--------|
| `/` | `OverviewPage` (new) | **Overview** |
| `/devices` | `DevicesPage` (moved from `/`) | **Devices** |
| `/devices/:deviceId` | `DeviceDetailPage` (extended) | — |
| `/alerts` | `AlertsPage` (new) | **Alerts** |

Existing internal links to devices (e.g. from `DevicesPage`) must be updated to `/devices/...`
where needed.

### 3.2 Data layer
- **`schema.d.ts` regenerated** from the backend OpenAPI (includes the 3 2C endpoints). It is a
  mechanical step (`openapi-typescript`), prerequisite for everything else.
- **TanStack Query hooks** on top of the typed `api` client, one per endpoint, tenant-scoped via
  `useTenant().activeId`:
  - `useTenantHealth()` → `GET /api/tenants/{tenant_id}/health`
  - `useAlerts({ active })` → `GET /api/tenants/{tenant_id}/alerts?active=`
  - `useDeviceMetrics(deviceId, metric, range)` → `GET .../devices/{device_id}/metrics?metric=&from=&to=&bucket=`
  - Query key namespacing per tenant (consistent with existing `["device", activeId, deviceId]`).
- **Time-range selector** (`1h` / `24h` / `7d`) → maps to `from`/`to`/`bucket`:
  `1h`→bucket 60s, `24h`→300s, `7d`→3600s. Keeps points below `MAX_POINTS` (5000) on the API side and
  produces smooth charts. A pure util `rangeToParams(range, now)` computes the parameters.

### 3.3 Pages and components
- **`OverviewPage`** (`/`): summary cards from `/health` (devices by status + total; number of
  active alerts) + **active alerts** list from `/alerts?active=true`, with link to device.
- **`DeviceDetailPage`** (extended): the existing device section + **health section** — status cards
  (status, last_seen, firmware) + **charts** with time-range selector:
  - CPU/mem/disk → time series (`cpu.pct`, `mem.pct`, `disk.pct`), + `uptime.seconds`
  - Interface traffic → `iface.bytes_in`/`iface.bytes_out` (multi-series by interface label),
    + `iface.up` (interface status)
  - Gateways → `gateway.rtt_ms`/`gateway.loss_pct`/`gateway.up` (by gateway label)
  - VPN → `vpn.up` (by tunnel label)

  *Metric names confirmed* against `backend/app/services/monitoring.py` (2A/2B poller).
- **`AlertsPage`** (`/alerts`): alert table with **active/historical** filter (toggle `active=true|false`),
  columns type/label/severity/opened/resolved, sorted by `opened_at` (API already sorts desc).
- **`MetricChart`** (reusable component): wrapper on Mantine Charts `LineChart`/`AreaChart`;
  takes a `MetricPoint[]` series (optionally multi-label → multi-series), with label/units.
  Transforms `{time,label,value}` points into the Mantine Charts data format.
- **Status components**: `HealthSummaryCards` (counts from `/health`), reusable `StatusBadge`/`DeviceStatusCard`.

### 3.4 Data flow & error handling
- Loading/error handled by TanStack Query → Mantine skeleton during load, Mantine `Alert` on
  error, **empty-state** when no data (e.g. device never polled → empty series: message
  "nessun dato ancora").
- Everything tenant-scoped: hooks read `activeId` from `useTenant()`; tenant change (via
  `TenantSwitcher`) invalidates/refetches queries (query keys include `activeId`).
- Metrics/alerts are read-only in 2D: no mutations (no CSRF needed for GETs).

## 4. Testing
- **MSW handlers** for `/metrics`, `/health`, `/alerts` added in tests via `server.use(...)`
  (the server is empty by default in `src/test/server.ts`).
- **Vitest + RTL** for page/component:
  - `OverviewPage`: cards show mock counts; active alert list rendered; empty-state.
  - `DeviceDetailPage`: charts render mock data; time-range selector changes the query;
    empty-state on empty series.
  - `AlertsPage`: active/historical toggle changes the request (`active=true|false`) and content.
  - `MetricChart`: correctly maps `MetricPoint[]` → Mantine Charts data (transformation test).
  - `rangeToParams`: pure util tested on all ranges.
- Mantine Charts renders SVG/`ResponsiveContainer`: tests assert presence of data/structures
  (series, labels, values in DOM), **not** pixels/dimensions. Where `ResponsiveContainer` has
  dimension issues in jsdom, mock dimensions or use fixed width/height props in tests
  (known Recharts/jsdom pattern).

## 5. Milestone breakdown (for the plan)
1. **Data layer**: `schema.d.ts` regeneration + hooks (`useTenantHealth`/`useAlerts`/`useDeviceMetrics`)
   + util `rangeToParams` (with tests) + install `@mantine/charts`.
2. **Base components**: `MetricChart` + `HealthSummaryCards`/status cards (with tests).
3. **`OverviewPage`** + routing/navbar reorganization (`/`=Overview, `/devices`=Devices,
   `/alerts`=Alerts) (with tests).
4. **`DeviceDetailPage` extended**: health section with essential+network charts + time-range selector
   (with tests).
5. **`AlertsPage`**: table + active/historical filter (with tests).

Each task = TDD implementation + review (spec + quality) subagent-driven.

## 6. Definition of "Done" (2D, and Phase 2)
- The navbar offers Overview / Devices / Alerts; routing is reorganized without breaking existing links.
- Overview shows fleet health summary + active alerts for the client.
- DeviceDetail shows status + charts (CPU/mem/disk, interface traffic, gateways, VPN) with
  time-range selector.
- Alerts page lists active and historical with filter.
- Everything tenant-scoped (tenant change refetches), with loading/error/empty-state.
- Frontend suite (Vitest) green; `tsc`/lint clean.
- **With 2D, Phase 2 is complete**: poller → storage → API → dashboard.

## 7. Non-goal / deferred
- **UI-side auto-refresh/polling** (live chart updates): MVP fetches on-load/range-change;
  `refetchInterval` is a follow-up improvement.
- **Chart export/print**, custom ranges (free date picker): MVP uses the 3 presets.
- **Continuous aggregate** on the API side for long ranges (2C debt): the `7d` selector uses on-the-fly bucket 3600s
  — acceptable; the materialized CAGG remains for Phase 5/optimization.
- **Enumerated "natural" buckets on the API side** (2C debt): the UI passes `bucket` in seconds, sufficient.
- **Alert management/actions** (manual ack/resolve from UI): 2D is read-only on alerts;
  opening/resolving remains with the poller.

## 8. Open Questions (non-blocking)
- **Metric names**: confirmed against `monitoring.py` (`cpu.pct`, `mem.pct`, `disk.pct`,
  `uptime.seconds`, `iface.bytes_in/out`, `iface.up`, `gateway.rtt_ms/loss_pct/up`, `vpn.up`). The
  real OPNsense endpoints are still to be validated (the user will provide them): *values* may
  be refined, but metric *names* remain the stable contract on the dashboard side.
- **Units/formatting** (bytes→MB/s, %, ms): presentation choices decided during the plan phase.

# OPNGMS — Phase 2: Monitoring & Health — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design), pending final spec review
- **Phase:** 2 of 5 of the OPNGMS roadmap
- **Depends on:** Phase 1 (Foundation+Auth+Device+Frontend) in `main`

---

## 1. Context

**Phase 2** gives OPNGMS status/health monitoring of the OPNsense fleet: a **polling**
engine that, on a schedule, queries each device via its REST API, collects metrics, stores
them as time series, updates status, generates alerts, and exposes them via API and dashboard.

**Log/events** (for Phase 5 reports) are Phase 3 — here we handle *state*
(polling), not *event chronologies* (syslog).

## 2. Design Decisions (Phase 2 brainstorming)

| Topic | Decision |
|-------|----------|
| Time-series storage | **TimescaleDB** (Postgres extension): hypertable, compression, continuous aggregates, native retention. Same DB/stack/migrations |
| Polling engine | **ARQ + Redis** (async job queue): cron → enqueue `poll_device` per device → concurrent workers. Integrated retry/backoff and observability |
| MVP metrics scope | **Essential + network**: up/down + last_seen, CPU/mem/disk, uptime, firmware+update; interfaces (status+traffic), gateways (status/RTT/loss), VPN tunnels (status) |

Platform constraints (Phase 1): Python/FastAPI, ~100-300 devices, direct API (pull), connector
`OpnsenseClient` (single HTTP boundary), app runtime as non-superuser role `opngms_app` with RLS.

## 3. Architecture

```
                 ┌─────────────┐   cron 60s    ┌──────────────┐
                 │ ARQ scheduler├──────────────►│ Redis (broker)│
                 └─────────────┘  enqueue       └──────┬───────┘
                                  poll_device(id)      │ consume
                                                ┌──────▼───────┐  OpnsenseClient   ┌─────────┐
                                                │ ARQ worker(s) ├──────HTTPS───────►│ OPNsense │
                                                └──────┬───────┘  (privileged)      └─────────┘
                                                       │ write metrics / status / alerts
                                                ┌──────▼─────────────────────┐
   React dashboard ──HTTP──► FastAPI ──RLS────► │ TimescaleDB (Postgres+TS)   │
   (charts)                  (read, opngms_app) │  metrics hypertable, alerts │
                                                └─────────────────────────────┘
```

- The **poller** (process `python -m app.worker`) is trusted backend infrastructure: it connects with
  the **owner** role (`ADMIN_DATABASE_URL`, bypasses RLS) to read ALL devices and write
  metrics/status/alerts.
- The **API** reads metrics/alerts as `opngms_app` (non-superuser) under **tenant-context** → the
  RLS filters by client. Same defense-in-depth as `devices`.

## 4. Data Model

### 4.1 Hypertable `metrics` (TimescaleDB)
Narrow + labeled, covers scalars and multi-dimensional:
```
metrics(
  time        TIMESTAMPTZ NOT NULL,
  device_id   UUID NOT NULL,        -- (no FK: hypertable; integrity managed by poller)
  tenant_id   UUID NOT NULL,        -- denormalized: per-client aggregations + RLS
  metric      TEXT NOT NULL,        -- e.g. 'cpu.load', 'mem.used_pct', 'iface.bytes_in', 'gateway.rtt_ms', 'vpn.up'
  label       TEXT NOT NULL DEFAULT '',  -- dimension: '' for scalars, 'igb0'/'WAN_GW'/'wg0' for multi-dim
  value       DOUBLE PRECISION NOT NULL
)
```
- `create_hypertable('metrics', 'time')`; index on `(tenant_id, device_id, metric, time DESC)`.
- **Continuous aggregate** `metrics_5m` (avg/max per metric+label, 5 min bucket) for long-period
  dashboards. **Retention policy**: raw dropped after N days (config, default 30); the
  continuous aggregate has longer retention.
- **RLS** on the hypertable keyed on `tenant_id` (poller owner bypasses; API filters). Added to
  `TENANT_TABLES` (existing `rls.py` module). `opngms_app` gets SELECT (grant; verify
  propagation to Timescale chunks).

### 4.2 Table `alerts` (relational control-plane, not a hypertable)
```
alerts(
  id          UUID PK,
  tenant_id   UUID NOT NULL,    -- RLS
  device_id   UUID NOT NULL FK devices ON DELETE CASCADE,
  type        TEXT NOT NULL,    -- 'device.down' | 'gateway.down' | ...
  label       TEXT,             -- e.g. gateway name
  severity    TEXT NOT NULL DEFAULT 'warning',
  opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,      -- NULL = active
  details     JSONB NOT NULL DEFAULT '{}'
)
```
- RLS keyed on `tenant_id`. Single *active* alert per `(device_id, type, label)` (partial
  unique constraint on `resolved_at IS NULL`). The poller opens/resolves alerts on state changes.

### 4.3 Current status on `Device`
The *current* status (up/down, last_seen, firmware_version) stays on the existing `Device`
fields, updated by the poller each cycle. *Current* metrics (latest CPU/mem/etc.) are derived
from the hypertable (`last()` from Timescale) — no separate "snapshot" table in the MVP.

## 5. Polling Engine (ARQ + Redis)

- **`app/worker.py`**: ARQ `WorkerSettings` with functions + cron jobs + Redis settings.
- **Cron `enqueue_device_polls`** (every `POLL_INTERVAL_SECONDS`, default 60): lists all devices
  (owner connection), enqueues `poll_device(device_id)` for each.
- **`poll_device(device_id)`**: loads the device, decrypts secrets (`crypto`), builds
  `OpnsenseClient`, collects metrics, writes to `metrics`, updates `Device.status`/`last_seen`/
  `firmware_version`, evaluates alerts (state transitions). Idempotent; ARQ **retry** with
  backoff on transient errors.
- **Concurrency/rate-limit**: ARQ worker `max_jobs` bounds global concurrency toward the
  OPNsense APIs.
- **Worker DB connection**: owner role (`ADMIN_DATABASE_URL`) — sees all devices, writes
  metrics/alerts bypassing RLS (it is trusted infrastructure, not user-facing).
- **docker-compose**: adds `redis` and `worker` services (in addition to `db` now TimescaleDB).

## 6. `OpnsenseClient` Connector Extensions

New async methods (one method per metric group), returning normalized dicts; they maintain
the **single HTTP boundary** principle and existing error normalization:
- `get_system_info()` → cpu/mem/disk/uptime
- `get_firmware_status()` (already exists) → version + available updates
- `get_interfaces()` → per interface: status, bytes in/out
- `get_gateways()` → per gateway: status, RTT, loss
- `get_vpn_status()` → per tunnel: up/down

⚠️ **Exact OPNsense endpoints TO VERIFY** against a real device (presumably under
`/api/diagnostics/...`, `/api/routes/gateway/status`, `/api/wireguard/...`, etc.). The abstraction and
tests (mock respx) do not change; the endpoint→metric mapping is confirmed in implementation.

## 7. Metrics/Health API (FastAPI, tenant-scoped)

Under `/api/tenants/{tenant_id}/...`, gated by `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../devices/{device_id}/metrics?metric=&from=&to=` → time series (from the continuous
  aggregate for long ranges, raw for short ranges) + last value.
- `GET .../health` → per-client summary: # reachable/unverified/unreachable devices, # active alerts.
- `GET .../alerts?active=true` → alerts (active or historical) for the client.

## 8. Frontend Dashboard (React + Mantine)

- **Per-device health view**: charts over time (CPU/mem, interface traffic), gateway/VPN status,
  last update. Charts library (Mantine Charts / Recharts — chosen during the plan phase).
- **Per-client overview**: fleet health summary + active alert list.

## 9. Milestone Breakdown
1. **2A — Infra + storage + core poller**: TimescaleDB+Redis in compose, migration (extension +
   `metrics` hypertable + retention + RLS), ARQ setup, poller (cron→`poll_device`), connector
   `get_system_info`, collection of **essential health** (up/down, CPU/mem/disk, uptime, firmware) +
   status update. *Definition of done:* a mocked device is "polled", metrics appear
   in the hypertable, status updates.
2. **2B — Network metrics + alerting**: interfaces/gateways/VPN connector + collection, alert
   engine (transitions → `alerts` table, open/resolve).
3. **2C — Metrics/health API**: per-device endpoint (series+last), per-client summary, alerts —
   tenant-scoped + RLS, with cross-tenant isolation tests.
4. **2D — Frontend dashboard**: per-device health views (charts) + per-client overview + alerts.

Each milestone = spec→plan→subagent-driven execution.

## 10. Testing
- **Poller**: `poll_device` tested with a mocked `OpnsenseClient` (respx) or injected fake
  client; verifies metric write to a test TimescaleDB + status update + alert opening.
  Connector with respx (as in Phase 1).
- **Storage**: tests run on a real TimescaleDB (the extension is needed for create_hypertable); the
  conftest creates the extension + hypertable in the test DB.
- **API**: tenant-scoped integration tests + **cross-tenant metric isolation** (one client cannot
  see another's metrics), via RLS as with devices.
- **Alerting**: state transitions (reachable→unreachable opens an alert; return resolves it).

## 11. Definition of "Done" (Phase 2)
- The worker polls the fleet on a schedule, with bounded concurrency and retry.
- Essential+network metrics flow into the TimescaleDB hypertable; device status updates.
- Alerts open/resolve on state changes.
- The API exposes metrics/health/alerts per client, isolated by RLS (tests prove it).
- The dashboard shows per-device health and per-client overview.

## 12. Non-goals / deferred
- Log/events and syslog ingest (Phase 3); config push (Phase 4); PDF reporting (Phase 5).
- Alert notification channels (email/webhook) — the MVP generates/exposes alerts; sending is later.
- Horizontal scaling beyond single-instance ARQ pool.
- User-configurable alert thresholds (MVP: fixed device-down/gateway-down rules).

## 13. Open Questions (non-blocking)
- **Exact OPNsense endpoints** for system/interfaces/gateways/VPN — to verify against a real
  device; mocked until then.
- **Grant on Timescale hypertable** for `opngms_app` (chunk propagation) — to verify in 2A.
- **Multiple cadences** (e.g. firmware/update every hour instead of 60s) — MVP: single cadence;
  refine with multiple ARQ crons if needed.
- **Frontend charts library** (Mantine Charts vs Recharts) — decided during the 2D plan phase.

# OPNGMS — Phase 3: Log/Event Ingest — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user has delegated decisions and authorized to proceed
- **Phase:** 3 of 5 of the OPNGMS roadmap
- **Depends on:** Phase 1 (Foundation+Auth+Device) and Phase 2 (Monitoring: poller, TimescaleDB, ARQ, RLS) in `main`
- **Enables:** Phase 5 (PDF Reporting — Attacks, Visited Sites)

---

## 1. Context

**Phase 3** gives OPNGMS **log/event ingest** from the OPNsense fleet: security events
(Suricata IDS/IPS alerts) and browsing activity (DNS queries) are collected, normalized, and stored
as time series, to feed the **periodic reports** of Phase 5 (the "Attacks" and
"Web Activity / visited sites" sections).

Unlike Phase 2 (current status/health via *polling*), here we collect **chronologies of
discrete events**. The PDF and rich visualization remain Phase 5; Phase 3 stops at
ingest + storage + query API (so that data is verifiable end-to-end).

## 2. Design Decisions (Phase 3 brainstorming)

| Topic | Decision |
|-------|----------|
| Transport | **Pull via API** (worker queries the OPNsense API), consistent with the outbound-only + SSRF architecture already built; no inbound listener, reuses worker/connector/RLS |
| MVP sources | **Both**: Suricata IDS/IPS (alerts/attacks) and DNS (visited sites) |
| MVP boundary | **Ingest + storage + query API** (PDF and frontend → Phase 5) |
| Cadence | **Separate ingest job** (default 300s), distinct from the metrics poller (60s) |
| Incrementality | **Cursor per (device, source) + idempotent deduplication** on pull |

## 3. Architecture

```
        ┌──────────────┐  cron 300s   ┌──────────────┐
        │ ARQ scheduler ├─────────────►│ Redis        │
        └──────────────┘ enqueue       └──────┬───────┘
                  ingest_device_events(id)     │ consume
                                        ┌──────▼─────────┐  OpnsenseClient   ┌──────────┐
                                        │  ARQ worker(s)  ├──────HTTPS───────►│ OPNsense │
                                        └──────┬─────────┘  (SSRF-guarded)    │ IDS, DNS │
                                               │ events (owner, bypass RLS)   └──────────┘
   FastAPI ──RLS──► opngms_app          ┌──────▼──────────────────────────┐
   GET .../events, .../events/top       │ TimescaleDB: events (hypertable),│
                                        │ ingest_cursors                   │
                                        └──────────────────────────────────┘
```

The ingest job is trusted backend infrastructure: it connects as **owner** (`ADMIN_DATABASE_URL`,
bypasses RLS) to read all devices and write events. The **API** reads as `opngms_app`
(non-superuser) under tenant-context → RLS filters by client, identical to metrics/alerts.

## 4. Data Model

### 4.1 Hypertable `events` (TimescaleDB)
Narrow + JSONB for source-specific fields (same principle as `metrics`):
```
events(
  time        TIMESTAMPTZ NOT NULL,   -- event timestamp (from source)
  device_id   UUID NOT NULL,
  tenant_id   UUID NOT NULL,          -- denormalized: RLS + per-client aggregations
  source      TEXT NOT NULL,          -- 'ids' | 'dns'
  category    TEXT NOT NULL DEFAULT '',-- e.g. 'alert' (ids), 'query' (dns)
  src_ip      TEXT NOT NULL DEFAULT '',-- initiator (internal client)
  dst_ip      TEXT NOT NULL DEFAULT '',
  name        TEXT NOT NULL DEFAULT '',-- signature (ids) / domain (dns)
  severity    TEXT NOT NULL DEFAULT '',-- ids: 1..3 / low-high
  action      TEXT NOT NULL DEFAULT '',-- alert|drop (ids), allowed|blocked (dns)
  event_key   TEXT NOT NULL,          -- natural dedup key (source id or content hash)
  attributes  JSONB NOT NULL DEFAULT '{}'  -- full normalized record (report flexibility)
)
```
- `create_hypertable('events', 'time')`; index on `(tenant_id, device_id, source, time DESC)`.
- **Deduplication**: **unique** index `(device_id, source, event_key, time)` (includes `time`, required by
  Timescale for unique constraints on the hypertable); insert with `ON CONFLICT DO NOTHING` → idempotent on
  overlapping polls, like the alert guard (2B).
- **RLS** keyed on `tenant_id` (added to `TENANT_TABLES`; worker owner bypasses, API filters).
- **Compression + retention** (default 90 days; events have higher volume than metrics).

### 4.2 Table `ingest_cursors` (internal worker state, NOT a hypertable, NOT user-facing)
```
ingest_cursors(
  device_id   UUID NOT NULL,
  source      TEXT NOT NULL,
  last_time   TIMESTAMPTZ,    -- watermark: last ingested event
  last_ref    TEXT,           -- opaque source reference (e.g. last id/offset), nullable
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (device_id, source)
)
```
- Written/read only by the worker (owner). **No RLS** (not exposed via API; it is internal state).
  Integrity: `device_id` references an existing device; on device deletion the cursor
  becomes an orphan but is harmless (or FK CASCADE — decided during the plan phase).

## 5. Ingest Pipeline

- **Cron `enqueue_event_ingests`** (every `INGEST_INTERVAL_SECONDS`, default 300): lists all devices
  (owner), enqueues `ingest_device_events(device_id)` for each.
- **`ingest_device_events(device_id)`**: loads device, decrypts secrets, builds `OpnsenseClient`;
  for each **source** (`ids`, `dns`): reads the cursor `(device, source)`, calls the connector
  method with `since = last_time` (with a small overlap `δ` to avoid missing edge events), normalizes,
  inserts into `events` (`ON CONFLICT DO NOTHING`), updates the cursor to the `max(time)` ingested.
  **Resilient**: an error from one source (`OpnsenseError`) is logged and skips that source, without
  failing the others or the job. Idempotent (cursor + dedup).
- **Concurrency/rate-limit**: bounded by the worker ARQ `max_jobs` (shared with the metrics poller).

## 6. `OpnsenseClient` Connector Extensions

New async methods (one method per source), returning lists of normalized dicts, maintaining
the **single HTTP boundary** + SSRF guard + existing error normalization:
- `get_ids_alerts(since)` → Suricata alerts: time, src_ip, dst_ip, signature, severity, action.
- `get_dns_events(since)` → DNS queries: time, client_ip, domain, action (allowed/blocked).

Each dict includes an `event_key` (source id if available, otherwise content hash) and the
raw `attributes`.

⚠️ **Exact OPNsense endpoints TO VERIFY** against a real device (IDS presumably
`/api/ids/service/queryAlerts` with pagination; DNS more uncertain — Unbound/Zenarmor). The abstraction and
tests (mock respx) do **not** change; the endpoint→field mapping is confirmed in implementation when
a real device is available. **Suricata is the solid source**; if the API does not expose DNS logs in
a usable way, **3B** will remain mocked until the real device is available (risk noted, not blocking for
storage/API).

## 7. Query API (FastAPI, tenant-scoped + RLS)

Under `/api/tenants/{tenant_id}/...`, gated by `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../events?source=&device_id=&from=&to=&limit=` → paginated event list (most recent first),
  with a defensive cap on `limit` (like the metrics endpoint 2C).
- `GET .../events/top?source=&field=src_ip|name&from=&to=&limit=` → top-N aggregation by field
  (prefigures Phase 5 report tables: top initiators / signatures / sites). Count per value.

## 8. Milestone Breakdown
1. **3A — Storage + ingest framework + Suricata**: `events` hypertable + RLS + migration; `ingest_cursors`
   table; cron + `ingest_device_events` with cursor/dedup; `get_ids_alerts` connector +
   IDS collection+normalization. *Done:* a mocked device is "ingested", IDS alerts appear
   in `events`, the cursor advances, re-polls do not duplicate.
2. **3B — DNS source**: `get_dns_events` connector + DNS collection+normalization in the same job.
3. **3C — Query API**: list + top-N endpoints, tenant-scoped + RLS, with cross-tenant isolation tests.

Each milestone = spec→plan→subagent-driven execution.

## 9. Testing
- **Ingest**: `ingest_device_events` tested with a mocked `OpnsenseClient` (respx) or injected fake;
  verifies write to `events`, cursor advancement, **idempotency** (re-run does not duplicate), resilience
  (error in one source does not block the other). On a test TimescaleDB (conftest creates the hypertable).
- **Connector**: respx as in Phase 1/2; field mapping on sample IDS/DNS payloads.
- **API**: tenant-scoped integration + **cross-tenant event isolation** via RLS (as with metrics/alerts).
- **Dedup**: two ingests with overlapping events → no duplicates (unique `ON CONFLICT`).

## 10. Definition of "Done" (Phase 3)
- The worker ingests events (IDS + DNS) from the fleet on a schedule, incrementally and idempotently.
- Normalized events flow into the `events` hypertable, isolated per tenant by RLS.
- The API exposes list + top-N of events per client, with isolation tests.
- Cursors advance; overlapping polls do not duplicate.

## 11. Non-goal / deferred
- **PDF Reporting** (Phase 5) and **frontend event view** (Phase 5).
- **Syslog push** (inbound listener): pull was chosen; push is a future evolution.
- **Alerts on events** (e.g. "too many attacks/hour"), correlation/SIEM, GeoIP enrichment.
- **Sources beyond IDS/DNS** (Squid proxy, flow/Zenarmor for Data Usage/Applications) — subsequent.

## 12. Open Questions (non-blocking)
- **Exact OPNsense endpoints** for IDS/DNS (and payload format) — to verify against a real device;
  mocked until then. 3B-DNS is the most at risk (uncertain DNS log API exposure).
- **`event_key`/dedup**: stable id provided by the source vs content hash — decided in 3A based on
  the real IDS payload; default is content hash of the normalized content.
- **Exact retention/compression** (90d raw?) — adjustable; may diverge by source.
- **`ingest_cursors` FK/cleanup** on device deletion — FK CASCADE vs harmless orphan cursor,
  decided in 3A plan.

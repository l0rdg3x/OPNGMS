# Syslog Phase 3 — Sub-project 3.1: Log-forwarding Provisioning UX (design spec)

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 3** (lifecycle & scale). Phase 3 is decomposed into four
independent sub-projects, each with its own spec → plan → build:
**3.1 Provisioning UX (this spec)** → 3.2 cert rotation/revocation → 3.3 scale (multi-node + `search_after`)
→ 3.4 MSP cross-tenant dashboards. Phases 1 (PR #65) and 2 (PR #66) are merged.

## Goal

Let an operator **enable / disable log forwarding for a device from the UI** — and see, at a glance,
whether logs are actually flowing — instead of calling the Phase-1 API by hand. A "Log forwarding"
card on the device detail page drives the existing
`GET/POST .../devices/{id}/log-forwarding[/enable|/disable]` endpoints, with the status response
extended to carry the certificate expiry and a **liveness** timestamp ("last log received").

The browser never talks to OpenSearch; liveness is resolved by the backend (the same isolation model
as Phase 2).

## Locked decisions (from brainstorming)

- **Status richness = status + liveness:** on/off, syslog target, cert fingerprint + expiry, **and** a
  "last log received" indicator (a `size=1` OpenSearch query for the device, reusing the Phase-2 client).
- Enable/disable **mutate the device** → behind a **confirm modal**, `CONFIG_PUSH` RBAC, CSRF (the API
  already enforces all three). Viewing status = `DEVICE_VIEW`.

## What already exists (Phase 1 — do not rebuild)

- API `app/api/log_forwarding.py`: `GET ""` (status, `DEVICE_VIEW`), `POST /enable` + `POST /disable`
  (`CONFIG_PUSH`, CSRF, audited, `OpnsenseError → 502`).
- `LogForwardingOut{device_id, enabled, cert_serial, cert_fingerprint, provisioned_at}`.
- Model `DeviceLogForwarding` already stores `enabled, cert_serial, cert_fingerprint,
  opnsense_*_uuid, provisioned_at, updated_at` (tenant-scoped, RLS).
- `provision_device(...)` / `deprovision_device(...)` and `SyslogCaService`.
- Phase-2 OpenSearch client `app/services/log_search.py` (`build_search_body`, `search_logs`,
  `LogSearchError`).

So this sub-project is **additive**: two new response fields, one stored column, one small backend
helper, and the frontend card. No new endpoints.

## Components

### 1. Backend — cert expiry capture (`app/services/syslog_ca.py`, `log_forwarding.py`, migration)

- Add `cert_not_after(cert_pem: bytes) -> datetime` to `syslog_ca.py` (parse the cert, return its
  `not_valid_after_utc` as an aware UTC datetime). Device certs are **not** deterministically
  re-derivable (serial + validity depend on issuance time), so the expiry must be captured at enable
  time, not recomputed.
- **Migration 0025:** add `cert_not_after TIMESTAMPTZ NULL` to `device_log_forwarding`.
- `provision_device(...)`: when it issues + stores the device cert, also store `cert_not_after`
  (parsed from the freshly issued cert). `deprovision_device` may leave it as-is (row is marked
  disabled / cleared per existing behaviour).

### 2. Backend — liveness helper (`app/services/log_search.py`)

- `async def latest_log_at(settings, *, tenant_id: uuid.UUID, device_id: uuid.UUID) -> datetime | None`
  — a minimal OpenSearch query: `build_search_body`-style body with the tenant + device `filter`,
  `size=1`, `sort=[{"@timestamp":"desc"}]`, `_source=["@timestamp"]`, **no** time-range requirement
  (we want the most recent doc ever). POST to `opngms-logs-*/_search?ignore_unavailable=true`. Returns
  the latest `@timestamp` parsed to an aware datetime, or `None` if there are no hits **or** OpenSearch
  is unreachable/errors (wrapped in `try/except` — liveness is best-effort, the card must still render).
  Reuse the same httpx pattern as `search_logs`; keep the tenant filter mandatory.

### 3. Backend — extend the status response (`app/schemas/log_forwarding.py`, `app/api/log_forwarding.py`)

- `LogForwardingOut` gains `cert_not_after: datetime | None = None` and `last_log_at: datetime | None = None`.
- `_out(row, ...)` maps `cert_not_after` from the row.
- `GET ""` status handler: after loading the row, **if `row` exists and `row.enabled`**, call
  `latest_log_at(settings, tenant_id=…, device_id=…)` and set `last_log_at` on the response. If
  disabled or no row, skip the OpenSearch call (`last_log_at = None`). The `enable`/`disable`
  handlers return `last_log_at = None` (no liveness round-trip on a mutation; the card refetches
  status afterwards).

### 4. Frontend — `LogForwardingCard` + hooks

- `frontend/src/logs/logForwardingHooks.ts`:
  - `useLogForwardingStatus(deviceId)` — `useQuery` GET status (typed from the OpenAPI client).
  - `useEnableLogForwarding(deviceId)` / `useDisableLogForwarding(deviceId)` — `useMutation` POST
    enable/disable; on success invalidate the status query.
- `frontend/src/components/LogForwardingCard.tsx` (rendered on `DeviceDetailPage`):
  - **Enabled** badge (green) / **Disabled** badge (grey); when enabled, a static
    "mTLS TLS syslog" label (the receiver host is deployment config, not per-device — we do **not**
    fetch or expose it here).
  - Cert: short fingerprint + expiry (`cert_not_after`), with an **amber "expires soon"** hint if the
    expiry is within 30 days and a **red "expired"** hint if past (display-only; rotation is 3.2).
  - **Liveness** dot + relative time from `last_log_at`: **green** if within 15 minutes, **amber** if
    within 24 hours, **grey "stale"** if older, **grey "unknown"** if `null` (OpenSearch unreachable or
    no logs yet). A one-line legend/tooltip explains the dot.
  - **Enable** / **Disable** buttons gated to `CONFIG_PUSH` roles (tenant_admin/operator), each behind
    a Mantine confirm modal ("This imports a client certificate and configures a TLS syslog target on
    the device." / "This removes the syslog target and certificate from the device."). Loading +
    error states; the box-side error (502 → `OpnsenseError` type name) shown as a sanitized alert.
  - For `DEVICE_VIEW`-only roles (read_only) the card is read-only (status + liveness, no buttons).
- `DeviceDetailPage.tsx`: mount the card (a new section/tab consistent with the existing Firmware/
  config-push sections).

### 5. Settings / wiring

No new settings and no new config exposed to the frontend. The card shows a static "mTLS TLS syslog"
label when enabled; the receiver host/port stay deployment config (already documented in the README).

## Data flow

Card mounts → `GET .../log-forwarding` → `{enabled, cert_fingerprint, cert_not_after, last_log_at, …}`
(backend computed `last_log_at` via OpenSearch only when enabled) → operator clicks Enable → confirm →
`POST /enable` → on success invalidate + refetch status → liveness begins to populate as logs arrive.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| OpenSearch unreachable / no logs yet | `last_log_at = null` → liveness "unknown" (grey); rest of the card renders normally |
| Enable/disable box failure (`OpnsenseError`) | API 502 (type name only) → card shows a sanitized "device rejected the change" alert; status unchanged |
| read_only caller | status + liveness shown; enable/disable buttons hidden (and 403 server-side if forced) |
| Device not in tenant | 404 (existing `_device` guard) |
| Cert expired / expiring | display-only hint; no action here (rotation is sub-project 3.2) |

## Security

- Enable/disable keep `CONFIG_PUSH` + CSRF + audit (unchanged). Status stays `DEVICE_VIEW`.
- `latest_log_at` keeps the **mandatory tenant filter** (same guarantee as Phase 2); device_id is the
  RLS-verified path device. No OpenSearch detail is leaked to the browser — only a timestamp.
- No secrets in the card: cert **fingerprint** (public) and expiry only; never the key.

## Testing

- **Backend unit:** `cert_not_after` parses a known cert; `latest_log_at` maps a `size=1` hit to a
  datetime and returns `None` on empty hits and on a 5xx (OpenSearch mocked via respx).
- **Backend API:** `GET` status includes `cert_not_after` + `last_log_at` when enabled (OpenSearch
  mocked); `last_log_at` is `null` when disabled (asserts **no** OpenSearch call); enable stores
  `cert_not_after`; RBAC unchanged (read_only can GET, cannot enable).
- **Frontend (vitest + MSW):** the card renders enabled/disabled status, the cert expiry, and the
  three liveness states (green/amber/grey by mocked `last_log_at`); the Enable confirm flow POSTs and
  refetches; read_only sees no buttons. `npm run build` green.

## Out of scope (later Phase-3 sub-projects)

- **3.2:** cert **rotation** (re-issue + re-import + new fingerprint/expiry) and **revocation**
  (the expiry/expired hints here are display-only).
- **3.3:** multi-node OpenSearch, `search_after` deep paging.
- **3.4:** MSP-admin cross-tenant dashboards (fleet-wide forwarding status, log volume, ingest health).

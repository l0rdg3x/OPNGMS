# Syslog → OpenSearch Log Pipeline — Phase 2 Design Spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 2 of 3** (search & investigation). Phase 1 (foundation — CA, provisioning, opt-in OpenSearch + syslog-ng) merged as PR #65. Phase 3 (lifecycle/scale) follows.

## Goal

Give operators a **tenant-scoped, backend-mediated way to search and investigate** the logs Phase 1
ships into OpenSearch — a search API plus an in-app "Logs" page. The browser **never** talks to
OpenSearch; the backend is the only client and **always** constrains every query to the caller's
tenant (the same isolation guarantee RLS gives the relational data).

## Locked decisions (from brainstorming)

- **Backend is the only OpenSearch client**, always tenant-scoped (browser never touches OpenSearch).
- **RBAC:** a new `Action.LOG_VIEW` granted to **tenant_admin + operator** (NOT read_only — forensic
  logs can be sensitive).
- **Query power:** a **Lucene `query_string`** (forensic power: `field:value`, boolean, wildcards) +
  structured filters (time range, device), **always ANDed inside a mandatory `tenant_id` filter** the
  client cannot escape, with guardrails (size/time caps, no leading wildcard).
- **Pagination:** `from`/`size` with caps (deep `search_after` paging is Phase 3 scale).

## Architecture

```
   Browser "Logs" page ──POST /api/tenants/{id}/logs/search──▶ FastAPI (LOG_VIEW)
     {query?, device_id?, from, to, page, size}                  build_query(...) ALWAYS injects
                                                                  filter:[term tenant_id=<path tenant>,
   ◀── {total, hits:[{ts, device_id, host, program, message}]} ──  range @timestamp gte/lte]
       row click → raw _source modal                              + must: query_string (guarded)
                                                                  + optional term device_id
                                                                        │ httpx (internal URL)
                                                                        ▼
                                              OpenSearch  GET opngms-logs-*/_search
```

## Components

### 1. Search service — `app/services/log_search.py`

Two concerns, both pure-ish and unit-testable (OpenSearch HTTP mocked):

- **`build_search_body(*, tenant_id, frm, to, query, device_id, page, size) -> dict`** — the OpenSearch
  request body. ALWAYS:
  - `query.bool.filter = [{"term": {"tenant_id": str(tenant_id)}}, {"range": {"@timestamp": {"gte": frm, "lte": to}}}]`
  - if `device_id`: append `{"term": {"device_id": str(device_id)}}` to `filter`.
  - if `query` (non-empty): `query.bool.must = [{"query_string": {"query": query, "default_field": "message", "allow_leading_wildcard": False, "analyze_wildcard": False, "lenient": True}}]`.
  - `sort = [{"@timestamp": "desc"}]`, `from = page*size`, `size = min(size, MAX_SIZE)`, `track_total_hits = True`.
  The `tenant_id` filter is a `filter` clause (ANDed); even a malicious `query_string` like
  `tenant_id:other` becomes a `must` that is ANDed with the filter → zero results, never a widen.
- **`search_logs(settings, *, tenant_id, frm, to, query, device_id, page, size) -> SearchResult`** —
  POSTs the body to `{opensearch_url}/opngms-logs-*/_search` via httpx (internal, plain HTTP), maps the
  response to `SearchResult(total: int, hits: list[LogHit])` where `LogHit = {id, timestamp, device_id,
  host, program, message, source: dict}` (`source` = the raw `_source`). Raises `LogSearchError` on
  transport/OpenSearch error.

**Guardrails** (constants/settings): `MAX_SIZE` (default 200), `MAX_RANGE_DAYS` (default 31). The API
validates `to > frm`, `to - frm <= MAX_RANGE_DAYS`, clamps `size`.

### 2. API — `app/api/logs.py`

`POST /api/tenants/{tenant_id}/logs/search` (RBAC `LOG_VIEW`; a **read** — no CSRF, since it changes no
state and same-origin responses aren't readable cross-site):
- body `LogSearchIn{ query: str = "", device_id: uuid|None = None, frm: datetime, to: datetime, page: int = 0, size: int = 100 }`.
- validates the range (400 on bad/oversized), validates `device_id` belongs to the tenant if set,
  calls `search_logs(... tenant_id=<path tenant>)`, maps `LogSearchError` → 502.
- returns `LogSearchOut{ total, hits: [LogHitOut{ id, timestamp, device_id, host, program, message, source: dict }] }`.
  A separate `GET …/logs/{doc_id}` is NOT needed — each hit carries its full `_source` inline as
  `source` (the table shows the columns; the modal shows `source`). *(Decision: return `source` inline
  to avoid a second round-trip; it's already tenant-filtered.)*

`tenant_id` is taken from the path (RBAC-verified via `require_tenant`), NEVER from the body.

### 3. RBAC — `app/core/rbac.py`

Add `LOG_VIEW = "log.view"` to `Action` and `Action.LOG_VIEW: {TENANT_ADMIN, OPERATOR}` to
`_TENANT_MATRIX` (superadmin always allowed; read_only excluded).

### 4. Frontend — a per-tenant "Logs" page

- `frontend/src/logs/logHooks.ts` — `useLogSearch()` mutation (POST search) typed from the OpenAPI client.
- `frontend/src/pages/LogsPage.tsx` — a `Title` + a query bar: a **time-range** control (Mantine
  `DatePickerInput`/presets — default last 24h), a **Lucene query** `TextInput`, a **device** `Select`
  (from `useTenantDevices`, "all" default), a Search `Button`. Results: a Mantine `Table` (Time
  [tenant tz], Device [name resolved from the device list], Program, Message [truncated]) with a row
  click opening a `Modal` showing the pretty-printed `source` JSON. A "Load more"/page control. Empty +
  error + loading states. Gated to `LOG_VIEW` roles (the page checks the tenant role like
  ReportSchedulePage; the nav item is shown for tenant_admin/operator).
- `frontend/src/components/AppShell.tsx` — route `/logs` + a nav item.

### 5. Settings — `app/core/config.py`

`log_search_max_size: int = 200`, `log_search_max_range_days: int = 31`. (`opensearch_url` already exists.)

## Data flow

Browser → `POST …/logs/search` → `require_tenant(LOG_VIEW)` resolves the tenant → validate range +
device → `build_search_body(tenant_id=<path>, …)` → httpx to OpenSearch → map hits → `LogSearchOut`.
Cross-tenant is structurally impossible: the tenant filter is injected from the RBAC-checked path.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| `to <= frm` or range > `MAX_RANGE_DAYS` | 400 (validation) |
| `device_id` not in tenant | 404 |
| `size` > `MAX_SIZE` | clamped to `MAX_SIZE` (not an error) |
| Malformed Lucene query | OpenSearch with `lenient:true` ignores unparseable bits; a hard parse error → 400 with a safe message |
| OpenSearch unreachable / 5xx | `LogSearchError` → 502 (no internal detail leaked) |
| No matches / index absent | empty `hits`, `total: 0` (200) — `opngms-logs-*` with `ignore_unavailable` so a fresh deployment with no logs yet returns empty, not 404 |
| read_only / cross-tenant caller | 403 / tenant-scoped (RLS on devices; the search tenant filter) |

## Security

- **Tenant isolation:** the `tenant_id` filter clause is injected from the **path tenant** (verified by
  `require_tenant(LOG_VIEW)`), never from the request body. A `query_string` cannot widen past a
  `filter` clause (it lands in `must`, ANDed) — so no cross-tenant escape, even with a crafted query.
- **Least privilege:** `LOG_VIEW` excludes read_only; only the backend reaches OpenSearch (internal
  network, not published); the browser gets only tenant-filtered results.
- **Query-abuse guardrails:** `allow_leading_wildcard:false` + `analyze_wildcard:false` (no `*foo`
  scans), `MAX_SIZE`, required + capped time range, `track_total_hits` bounded by the time window.
- No secrets in responses/logs; the `source` returned is the log doc (already tenant-scoped), no creds.

## Testing

- **`build_search_body` (unit, no I/O):** the `tenant_id` filter + `@timestamp` range are ALWAYS
  present; `device_id` adds a filter; a non-empty `query` adds a guarded `query_string`
  (allow_leading_wildcard false); an injected `tenant_id:other` in the query stays in `must` (filter
  unchanged); `from = page*size`, `size` clamped to `MAX_SIZE`.
- **`search_logs` (unit, OpenSearch mocked via respx):** maps an OpenSearch hits response to
  `SearchResult`/`LogHit` (id, timestamp, device_id, host, program, message, source); a 5xx → `LogSearchError`.
- **API:** RBAC (operator allowed, read_only 403, cross-tenant 404 device / tenant-scoped), range
  validation (400), the OpenSearch call mocked, the tenant filter asserted to carry the path tenant.
- **Frontend (vitest + MSW):** the Logs page runs a search (POST captured, asserts the body shape),
  renders rows, opens the raw-doc modal; the device filter populates; LOG_VIEW gating (read_only sees a
  forbidden state). `npm run build` green.

## Build phases (informs the plan; one cohesive milestone)

- **A — backend:** RBAC `LOG_VIEW`; settings; `log_search.py` (`build_search_body` + `search_logs`);
  the search API + schemas; tests.
- **B — frontend:** `logHooks.ts`, `LogsPage.tsx`, route + nav, regen OpenAPI, tests + build gate.

## Out of scope (Phase 3)

- Cert **rotation/revocation** + the provisioning **UX button** (today provisioning is the API only).
- **Multi-node** OpenSearch, `search_after` deep paging, saved searches, MSP-admin cross-tenant
  dashboards, log-volume/retention analytics.
- Field-level redaction / column customization beyond the fixed table.

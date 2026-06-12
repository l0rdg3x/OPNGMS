# Syslog Phase 3.3 — Scale: deep paging + multi-node config (design spec)

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 3**, sub-project **3.3 of 4**
(3.1 provisioning UX #67; 3.2 cert lifecycle #68; **3.3 scale — this spec**; 3.4 MSP dashboards).

## Goal

Two scale deliverables for the tenant log search shipped in Phase 2:

1. **Deep paging (`search_after` + PIT)** — let an operator page through a result set of any size.
   Today the Logs UI sends `page:0, size:100` and shows only the first 100 hits; the backend's
   from/size paging is capped at OpenSearch's 10 000-document result window. This replaces both with a
   **Point-In-Time (PIT) + `search_after`** cursor that is unbounded and stable across the
   second-granularity `@timestamp` ties our logs produce.
2. **Multi-node OpenSearch config** — ship a 3-node cluster compose override + a replica-enabled index
   template + ops docs, so a deployment can run OpenSearch with high availability. (App behaviour is
   unchanged — the backend talks to the same URL regardless of node count.) HA is **verified at the
   consolidated staging bring-up**, not in CI.

## Locked decisions (from brainstorming)

- **PIT + `search_after`**, not from/size and not PIT-less. Rationale: our `@timestamp` is
  second-precision, so many docs tie within a second — `search_after` needs a stable tiebreaker, and
  `_id` is not sortable without fielddata. PIT gives the free `_shard_doc` tiebreaker **and** a
  consistent snapshot across pages. The backend stays **stateless**: the cursor (`{pit_id, after}`)
  lives in the client and round-trips through the API.
- **Scope = `search_after` (CI-verifiable) + multi-node config (shipped, bring-up-verified).** CA
  rotation / CRL / the bring-up itself remain their own backlog items.

## What exists (Phase 2 — to evolve)

- `app/services/log_search.py`: `build_search_body(*, tenant_id, frm, to, query, device_id, page, size)`
  (from/size + `sort:[@timestamp desc]`), `search_logs(...) -> SearchResult{total, hits}`,
  `LogHit{id,timestamp,device_id,host,program,message,source}`, `LogSearchError`, `latest_log_at`.
- `app/api/logs.py`: `POST /api/tenants/{id}/logs/search` (`LOG_VIEW`), with a `from+size > MAX_RESULT_WINDOW`
  deep-paging guard and `size` clamp.
- `app/schemas/logs.py`: `LogSearchIn{query, device_id, frm, to, page, size}`, `LogHitOut`, `LogSearchOut{total, hits}`.
- `frontend/src/pages/LogsPage.tsx`: sends `page:0, size:100`, renders the hits table; no pager.
- `docker-compose.logs.yml`: single-node OpenSearch (`discovery.type: single-node`).
  `deploy/opensearch/index-template.json`: `number_of_shards:1, number_of_replicas:0`.

## Components

### 1. Search service — `app/services/log_search.py` (evolve)

- **`open_pit(settings) -> str`** — `POST {opensearch_url}/opngms-logs-*/_pit?keep_alive=2m`
  (`ignore_unavailable=true`); returns the `pit_id`. Raises `LogSearchError` on transport error.
- **`build_search_body(*, tenant_id, frm, to, query, device_id, size, pit_id, search_after=None) -> dict`** —
  the same tenant + range + optional-device `filter` and guarded `query_string` `must` as Phase 2
  (tenant filter ALWAYS injected), PLUS:
  - `"pit": {"id": pit_id, "keep_alive": "2m"}` (so the URL is `{url}/_search` with **no** index path),
  - `"sort": [{"@timestamp": "desc"}, {"_shard_doc": "asc"}]` (the `_shard_doc` tiebreaker is only
    available under PIT),
  - `"size": min(size, MAX_SIZE)`, `"track_total_hits": True`,
  - `"search_after": search_after` **only** when continuing (omitted on the first page; no `from`).
- **`@dataclass LogCursorData{pit_id: str, after: list}`** (internal) — the next-page cursor.
- **`search_logs(settings, *, tenant_id, frm, to, query, device_id, size, cursor=None) -> SearchResult`** —
  if `cursor is None`: `pit_id = await open_pit(...)`, `search_after = None`; else reuse
  `cursor["pit_id"]` + `cursor["after"]`. POST the body to `{url}/_search`. Map hits (each OpenSearch
  hit carries a `sort` array — store the LAST hit's `sort`). Build
  `next_cursor = {"pit_id": <pit id from the response, falling back to the request pit_id>, "after": <last hit sort>}`
  **iff** `len(hits) == effective_size` (a full page implies there may be more); else `None`.
  Return `SearchResult{total, hits, next_cursor}`. `_shard_doc`/PIT errors (expired/garbage cursor) →
  `LogSearchError`.
- **Remove** the `page` parameter and the `MAX_RESULT_WINDOW` deep-paging guard usage from the search
  path. Keep `MAX_SIZE` (size clamp) and `latest_log_at` unchanged. `MAX_RESULT_WINDOW` may be dropped
  if nothing else references it.

`SearchResult` gains `next_cursor: dict | None`.

### 2. API + schemas — `app/api/logs.py`, `app/schemas/logs.py`

- `LogSearchIn`: **drop `page`**; add `cursor: LogCursor | None = None`, where
  `LogCursor{pit_id: str = Field(max_length=8192), after: list[Any]}`. Keep `query, device_id, frm, to, size`.
- `LogSearchOut`: add `next_cursor: LogCursor | None = None`.
- The endpoint: keep the range validation (400) + device-in-tenant (404) + `LogSearchError → 502` +
  size clamp; **remove** the deep-paging 400 guard. Pass `cursor=body.cursor.model_dump() if body.cursor else None`
  to `search_logs`; return `next_cursor=res.next_cursor`. Tenant filter still injected from the PATH.
- A garbage/expired cursor surfaces as a `LogSearchError` → 502; the UI treats any failure as
  "restart your search". *(Decision: 502 not 400 — we don't parse OpenSearch's PIT error; the UI just
  re-runs from page 1. Keeps the API simple and leaks nothing.)*

### 3. Frontend — `frontend/src/logs/logHooks.ts`, `frontend/src/pages/LogsPage.tsx`

- `useLogSearch()` accepts the optional `cursor` and returns `{total, hits, next_cursor}`.
- `LogsPage`: state `hits: LogHitOut[]`, `cursor: LogCursor | null`, `total`. A **new search**
  (Search button) resets `hits=[]`, `cursor=null` and loads page 1. A **"Load more"** button — shown
  only when the last response's `next_cursor != null` — fetches with that cursor and **appends** to
  `hits`. The match count + a "showing N of total" line. The raw-doc modal and role gating are
  unchanged. On a search error, show the existing alert (the user re-runs the search).

### 4. Multi-node OpenSearch config (shipped; HA verified at bring-up)

- **`docker-compose.logs.multinode.yml`** — a 3-node cluster override: `opensearch-n1/-n2/-n3` (same
  image/volumes pattern as the single node), `cluster.name`, `node.name` per node,
  `discovery.seed_hosts: opensearch-n1,opensearch-n2,opensearch-n3`,
  `cluster.initial_cluster_manager_nodes: opensearch-n1,opensearch-n2,opensearch-n3` (replaces
  `discovery.type: single-node`); `OPENSEARCH_URL` points the receiver/backend at `opensearch-n1`.
  Documented as **mutually exclusive** with the single-node `docker-compose.logs.yml` OpenSearch service.
- **`deploy/opensearch/index-template.multinode.json`** — `number_of_shards: 2, number_of_replicas: 1`
  (shards distribute and replicate across nodes → a node loss keeps the index green/available); same
  mappings + ISM policy as the single-node template.
- **README ops section** — when/how to run multi-node, the shard/replica rationale, that the
  single-node and multi-node OpenSearch services are alternatives, and that HA is verified at the
  consolidated staging bring-up.
- **CI** — a lightweight test that the new compose YAML and the new index-template JSON parse (no
  cluster bring-up).

## Data flow

Search → (no cursor) backend `open_pit` → `_search` (PIT + sort + tenant filter) → map hits + compute
`next_cursor` → UI renders + shows "Load more" if `next_cursor`. Load more → same with
`cursor={pit_id, after}` → append. New search → reset, new PIT.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| `to <= frm` / range > max | 400 (validation) — unchanged |
| `device_id` not in tenant | 404 — unchanged |
| OpenSearch unreachable / PIT open fails | `LogSearchError` → 502 |
| Expired / garbage cursor (PIT gone) | OpenSearch errors → `LogSearchError` → 502; UI re-runs the search from page 1 |
| Last page (`len(hits) < size`) | `next_cursor = null` → UI hides "Load more" |
| `size` > MAX_SIZE | clamped (not an error) |

## Security

- **Tenant isolation unchanged:** every `_search` body re-injects the `tenant_id` filter from the
  RBAC-verified PATH tenant — PIT only snapshots segments, it does **not** bypass the query filter. A
  crafted/foreign `pit_id` in the cursor still returns only tenant-filtered hits (and most likely just
  errors). `LOG_VIEW` (tenant_admin+operator) gating unchanged.
- The `pit_id` exposed to the browser is an opaque OpenSearch handle, length-capped in the schema; it
  cannot widen scope. No secrets in responses.
- Multi-node OpenSearch stays on the internal network (not published), same trust boundary as Phase 1;
  inter-node transport TLS is out of scope (internal-only).

## Testing

- **`build_search_body` (unit):** PIT block present; `sort` carries `@timestamp desc` + `_shard_doc`
  tiebreaker; tenant + range filters ALWAYS present; `search_after` present only when continuing and
  absent (no `from`) on page 1; size clamped; an injected `tenant_id:other` query stays in `must`.
- **`search_logs` (unit, respx):** page 1 opens a PIT then searches; maps hits + `next_cursor` (full
  page → cursor with the last hit's `sort`; partial page → `next_cursor=None`); a continuation reuses
  the cursor's `pit_id` + `after`; a 5xx → `LogSearchError`.
- **API:** cursor round-trips (request `cursor` → body `search_after`/`pit`); tenant filter carries the
  path tenant; range 400; device 404; no more deep-paging 400; `LogSearchError → 502`.
- **Frontend (vitest + MSW):** a search renders page 1 + shows "Load more" when `next_cursor`; "Load
  more" appends the second page and (on `next_cursor:null`) hides itself; a new search resets; LOG_VIEW
  gating unchanged. `npm run build` green.
- **Config:** a test asserts `docker-compose.logs.multinode.yml` and `index-template.multinode.json`
  parse and carry the expected keys (`number_of_replicas: 1`; three `opensearch-n*` services).

## Out of scope

- The actual multi-node **HA bring-up verification** (consolidated staging session with 3.2-bis CRL +
  the Phase-1 field-shape check).
- PIT-less paging, saved searches, server-side cursor storage, inter-node TLS, cross-tenant search.

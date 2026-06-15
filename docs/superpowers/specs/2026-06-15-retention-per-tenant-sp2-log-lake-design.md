# Per-tenant retention — Sub-project 2 (log lake) — Design

**Date:** 2026-06-15
**Status:** Approved (design); ready for implementation plan.
**Branch:** `feat/retention-loglake`
**Milestone:** "Retention settings" — SP-2 completes it. SP-1 (perimeter/events/metrics, all merged, UNRELEASED)
provides the foundation; SP-2 adds the **log lake** (raw syslog in OpenSearch) as the 4th retention store with
a global default + per-tenant override. After SP-2 merges + an end-to-end staging verification, **v0.11.0**
ships (SP-1 + SP-2 together) and the README + Wiki docs are refreshed
([[retention-settings-milestone]], [[docs-refresh-after-retention-sp2]]).

## Why the log lake is the hard one

The other three stores are Postgres tables purged by a tenant-aware `DELETE`. The log lake is **OpenSearch**:
- syslog-ng currently writes a **single shared daily index** `opngms-logs-${YEAR}.${MONTH}.${DAY}`
  (`deploy/syslog-ng/syslog-ng.conf`); `tenant_id` is a *field* on each doc but not in the index name.
- retention is a **global** ISM policy `opngms-logs-retention` (deletes indices older than
  `{{RETENTION_DAYS}}d` ← `log_retention_days`), attached to `opngms-logs-*`, applied once at bootstrap
  (`backend/app/cli.py`). ISM deletes **whole indices by age** → cannot be per-tenant on a shared index.

The enabler (already in place): syslog-ng extracts `tenant_id = ${.tls.x509_o}` (the device cert O=) and
**refuses logs without it**. So per-tenant indices are achievable by putting `tenant_id` in the index name.

## Goals

1. A **global default** `log_lake_retention_days` (superadmin) + a **per-tenant override** (`log_lake`), using
   the SP-1 registry + `tenant_retention` + resolver — the same model as the other three stores.
2. Enforcement: each tenant's log indices are deleted at **its** effective retention.
3. Defaults preserve today's behavior (30 days global).
4. The log lake is **optional** (only the `logs`/`full` compose runs it) — every change must no-op gracefully
   when OpenSearch isn't deployed/reachable.

## Non-goals

- No change to the **report ↔ retention guard**: reports draw from events/metrics/perimeter, NOT the log lake,
  so `log_lake` never bounds a report (it stays OUT of `SECTION_STORES`).
- No multi-node / HA / ILM-rollover redesign of the log lake (that's the separate syslog Phase-3 backlog).
- No re-indexing of existing shared `opngms-logs-DATE` data into per-tenant indices (they age out — see below).

## Design decisions (user-approved 2026-06-15)

- **Worker job** enforcement (NOT per-tenant ISM policies).
- **It must be tested end-to-end** — a local log-lake bring-up (OpenSearch + syslog-ng via the `logs` compose
  overlay) verifies routing + deletion before v0.11.0, in addition to unit tests.

## Architecture

### 1. Per-tenant index naming (syslog-ng)

Change the `d_opensearch` destination URL in `deploy/syslog-ng/syslog-ng.conf`:

```
url("`OPENSEARCH_URL`/opngms-logs-${tenant_id}-${YEAR}.${MONTH}.${DAY}/_doc")
```

`tenant_id` is a lowercase UUID (from the cert O=) — valid in an OpenSearch index name. The existing
`f_has_tenant` filter already drops logs with no tenant_id, so the index name can never be malformed
(`opngms-logs--DATE`). The index template `opngms-logs` (pattern `opngms-logs-*`, for mappings) still matches
the new names, so mappings are unchanged. Search/aggregation globs `opngms-logs-*`
(`log_fleet.py`/`log_search.py`) — unchanged, covers both new per-tenant and any legacy indices.

### 2. Worker delete job (owns retention)

A new daily worker cron `purge_log_lake` (mirrors the SP-1 purges; the worker already runs as the DB owner
and can read every tenant's overrides):

1. If `opensearch_url` is unset → return (log lake not deployed; no-op).
2. `GET {opensearch_url}/_cat/indices/opngms-logs-*?format=json&h=index` (httpx, the same client style as
   `cli.py`/`log_fleet.py`). On connection error → log a warning + return (best-effort).
3. For each index, parse the name `opngms-logs-<tenant_id>-<YYYY>.<MM>.<DD>`:
   - **tenant-tagged** (`<tenant_id>` is a valid UUID): effective retention =
     `effective_retention_days("log_lake", global_default=cfg["log_lake_retention_days"], tenant_override=<that tenant's overrides>)`.
   - **legacy date-only** (`opngms-logs-<YYYY>.<MM>.<DD>`, no tenant segment): use the **global** default.
   - Read the global config once + all tenant overrides once (hoisted, like the SP-1 warnings helper) —
     load a `{tenant_id: overrides}` map up front via the owner session (no RLS — owner sees all
     `tenant_retention` rows).
4. If the index's date < `today − effective_days` → `DELETE {opensearch_url}/<index>`. Count + log.
Each delete is best-effort/independent (one failure doesn't abort the sweep).

### 3. Remove the global ISM (the worker is now the authority)

`backend/app/cli.py` stops applying the `opngms-logs-retention` ISM policy and removes any pre-existing one so
it can't keep deleting per-tenant indices at the global age (which would violate a longer per-tenant override):
- keep applying the **index template** (mappings);
- `DELETE {opensearch_url}/_plugins/_ism/policies/opngms-logs-retention` (ignore 404), and detach it from
  existing indices if attached (`POST /_plugins/_ism/remove/opngms-logs-*`, best-effort) — exact calls
  confirmed at the bring-up. (Mirrors SP-1 removing the native TimescaleDB policies.)
- `deploy/opensearch/ism-policy.json` is retired/removed and the `docker-compose.logs*.yml` / deploy docs
  updated to note the worker now owns retention.

### 4. `log_lake` as the 4th retention store (reuse SP-1)

- Add `"log_lake"` to `RETENTION_STORES` (`app/services/retention.py`) — makes it a valid per-tenant override
  key + a registry-defaulted store. `tenant_retention.overrides` is already a store-agnostic JSONB map.
- Registry knob `log_lake_retention_days` (`app/services/runtime_settings.py`), group `retention`,
  `active=True`, bounds 1–3650, `default = lambda s: s.log_retention_days` (bridges the existing
  `LOG_RETENTION_DAYS` env so behavior + back-compat are preserved; no Settings field rename).
- The per-tenant API (`GET/PUT /api/tenants/{id}/retention`) already validates against `RETENTION_STORES`,
  so it accepts `log_lake` automatically; `_defaults` returns it too.

### 5. Report guard stays correct

`log_lake` is **not** added to `report_retention.SECTION_STORES`, so `report_range_bound` never mins over it
and no report is ever bounded by log-lake retention. Refine the **global-impacts trigger**: the
impacted-tenants scan in `update_runtime_settings` must fire only when a **report-bounding** store
(perimeter/events/metrics) is lowered — introduce `REPORT_BOUNDING_STORES = ("perimeter","events","metrics")`
and key the `lowered` scan off that, so lowering `log_lake_retention_days` doesn't pointlessly enumerate
tenants (it can never produce a report impact).

### 6. Frontend

- The global **Runtime settings** "retention" group auto-renders the 4th knob once `log_lake_retention_days`
  is active — add its i18n `system.runtime.items.log_lake_retention_days` ({label, help}).
- The per-tenant **Retention card** lists `STORES` hardcoded as `["perimeter","events","metrics"]` — add
  `"log_lake"` + the `retention.stores.log_lake` label. i18n across all 12 locales for the new keys.
- The per-tenant **warnings** are unaffected (log_lake never produces a schedule warning).

## Testing

- **Unit (no infra):** the `purge_log_lake` index-name parser (tenant-tagged vs legacy vs non-matching),
  the per-tenant cutoff selection, and the delete decision — with OpenSearch HTTP **mocked** (respx/httpx
  mock): assert it deletes the right indices and skips fresh ones, uses each tenant's effective retention,
  and no-ops when `opensearch_url` is unset. Registry/API/UI tests as in SP-1 (the per-tenant API now also
  round-trips `log_lake`; the card renders the 4th input).
- **End-to-end bring-up (required before v0.11.0):** bring up the `logs` compose overlay locally (OpenSearch
  + syslog-ng with a provisioned device cert), send a log, and verify: (a) it lands in
  `opngms-logs-<tenant_id>-<date>`; (b) `log_fleet`/search still find it via `opngms-logs-*`; (c) the
  `purge_log_lake` job deletes an artificially-old per-tenant index at that tenant's retention and respects a
  per-tenant override; (d) the legacy date-only path. Document the exact OpenSearch ISM-removal calls
  confirmed here. (This is the syslog Phase-3 staging bring-up — [[syslog-phase3-deferred-backlog]].)

## Invariants / security

- The worker job runs as the owner (reads all `tenant_retention` overrides) and talks to OpenSearch over the
  internal network (same trust boundary as today's ISM bootstrap; no auth, internal-only). No user-facing
  path. No new secret. RLS unaffected (override reads are owner-side in the worker, exactly like SP-1 purges).
- `tenant_id` in the index name is a server-derived UUID (cert O=), never client-supplied free text → no
  index-name injection.

## Decomposition (PRs within SP-2)

1. **PR1 — Backend.** syslog-ng per-tenant index URL + `purge_log_lake` worker job + cron + cli.py ISM removal
   + retire `ism-policy.json` + `log_lake` in `RETENTION_STORES` + `log_lake_retention_days` registry knob +
   `REPORT_BOUNDING_STORES` refinement + unit tests (mocked OpenSearch).
2. **PR2 — Frontend.** `log_lake` in the Retention card `STORES` + the global item i18n + card store i18n
   (12 locales) + `gen:api` (no new endpoint, but `RETENTION_STORES` surfaces via the typed defaults) + tests.
3. **E2E bring-up verification** (local `logs` compose) — gate before release; fix anything the real
   OpenSearch/syslog-ng surfaces.
4. **Release v0.11.0** (SP-1 + SP-2) + CHANGELOG, **then** the README + Wiki refresh (audit viewer + retention).

## Risks / open items

- **Existing shared `opngms-logs-DATE` data** keeps the legacy name; the worker applies the global retention
  to it and it ages out. No re-indexing. Acceptable.
- **ISM removal is the delicate real-OpenSearch step** — exact `_ism/remove` + policy-delete behavior varies
  by OpenSearch version; the bring-up confirms it (a "RUNTIME VERIFICATION REQUIRED" item until then).
- **Index count / `_cat/indices` cost** is one daily call + N deletes; fine. If a fleet ever has a huge number
  of per-tenant daily indices, switch the listing to a date-bounded pattern. Noted, not optimized now.
- **`log_lake` retention has no report-guard interaction** by design — double-checked so the report block /
  warnings / impacts never reference it.

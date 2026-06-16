# Backend Performance — Design Spec

**Date:** 2026-06-16
**Status:** Approved (design); writing the implementation plan next.
**Milestone:** Performance + refactor — **sub-project 2 of 4** (after the connector factory; before
frontend bundle + large-file splits). Measure-first, behavior-preserving, incremental PRs.

## Goal

Three measured backend-perf wins, each its own PR:

1. **PR1 — Singleton ARQ pool** (the flagged tech-debt).
2. **PR2 — Index audit** (measured; additive migration).
3. ~~**PR3 — Reporting per-device query fan-out**~~ — **DESCOPED** after measuring (queries are
   only 4% of report-gen; the bottleneck is CPU rendering, and report gen is async). See PR3 below.

## Measured findings

- **`app/core/queue.py::enqueue` opens one ARQ pool per call** (`create_pool` … `aclose` every call). Used
  by **7 API routers** (config, devices, log_forwarding, firmware, profiles, report_schedules, templates)
  on request-side enqueues (apply, send-now, rotate, schedule…). Each request pays a full Redis pool
  connect/teardown. (The worker cron fan-out does NOT use this — it reuses the worker's `ctx["redis"]`, so
  no pool churn there.)
- **`app/services/reporting/context.py:545` `for dev in devices:`** issues ~5–10 aggregator queries **per
  device, sequentially** (`health_summary`, `alerts_in_range`, `gateway_quality`, per-device timeline/top,
  …). A fleet report runs `O(devices × queries)` round-trips in series — the dominant report-gen latency.
- Indexes: the `events` hypertable is well-indexed (keyset). The other hot aggregator/RBAC/perimeter
  queries need an EXPLAIN/schema audit to confirm no sequential scans on common filters.

## PR1 — Singleton ARQ pool

A process-level, lazily-created, reused `ArqRedis` pool, replacing the per-call create/close.

```python
# app/core/queue.py
import asyncio
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

_pool: ArqRedis | None = None
# Eager lock: since Python 3.10 asyncio.Lock binds to the loop on first acquire (not construction), so a
# module-level lock is import-safe and avoids the TOCTOU race a lazily-built lock would have.
_pool_lock = asyncio.Lock()


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        async with _pool_lock:
            if _pool is None:                      # double-checked: only one pool is ever created
                _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue(name: str, *args, defer_until: datetime | None = None) -> None:
    pool = await _get_pool()
    kwargs = {"_defer_until": defer_until} if defer_until is not None else {}
    await pool.enqueue_job(name, *args, **kwargs)


async def close_pool() -> None:
    """Close the shared pool (app shutdown / test teardown). Idempotent."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
```

- **Lifecycle:** `app/main.py` `lifespan` calls `await close_pool()` after `yield` (graceful shutdown). The
  pool is created lazily on the first real `enqueue()` — so startup/import needs no Redis (tests still pass
  without Redis: they override `get_enqueuer` → `_noop_enqueue`, never touching `_get_pool`).
- **Loop-binding:** the cached `ArqRedis` binds to the event loop that created it — correct for the single
  uvicorn process loop. The worker is unaffected (it uses `ctx["redis"]`, not this module).
- **Concurrency:** an eager module-level `asyncio.Lock` (double-checked) ensures concurrent first-enqueues
  create exactly one pool (no leak); the lock binds to the loop on first acquire (Python 3.10+).

## PR2 — Index audit (measured) — RESULT

Audited every hot read path's filter/sort columns against the live index set (`pg_indexes`). The schema is
**already well-indexed**, so PR2 adds exactly **one** missing index:

- **`events`** — `ix_events_tenant_device_source_time` + the keyset index cover the timeline + per-device
  rollups. ✅
- **`metrics`** — `ix_metrics_tenant_device_metric_time` covers `health_summary`/`gateway_quality`. ✅
- **`perimeter_attacker`** — `ix_perimeter_attacker_rank (tenant_id, kind, last_seen DESC)` covers
  `perimeter_top`. ✅
- **RBAC** — `uq_membership_user_tenant` (user-leading), `ix_group_members_user_id`, `group_grants` all
  cover the resolution. ✅  · **audit_log / generated_reports** — composite indexes already present. ✅
- **`alerts`** — ❌ **the one gap.** `alerts_in_range` runs `WHERE tenant_id + device_id + opened_at range
  ORDER BY opened_at DESC` once per device per report, but `alerts` had only single-column
  `device_id`/`tenant_id` indexes → a full per-device scan + sort. **Add
  `ix_alerts_tenant_device_opened (tenant_id, device_id, opened_at)`** (mirrors the tenant-device-time
  pattern on `config_changes`/`config_snapshots`/`firmware_actions`).

Delivered as a **forward-only migration `0041`** + the matching model `Index` (kept in sync) + a test
asserting the index columns. Additive only — no data change, no query rewrite. **NOT added:** a
`(tenant_id, source, time)` index on the `events` hypertable for the tenant-wide-by-source rollups — the
write cost on the highest-insert table is not justified by infrequent, time-bounded report rollups (the
existing indexes serve them acceptably).

## PR3 — Reporting per-device fan-out (measured) — DESCOPED

**Measured** an all-sections report for an 8-device tenant (`before/after_cursor_execute` instrumentation):

| metric | value |
|---|---|
| queries | **171** (~21 / device, all sequential) |
| **query time** | **52 ms — 4% of wall** |
| **render / CPU** | **1154 ms — 96% of wall** (per-device line-chart SVGs, choropleth, Jinja) |
| wall | 1206 ms · html 270 KB |

The per-device query fan-out is real (~21 sequential queries × N devices) **but it is NOT the report-gen
bottleneck — it is only 4% of wall time.** The dominant cost (96%) is the **CPU-bound SVG/chart rendering +
HTML assembly**. Even on a remote DB (≈2 ms/round-trip → ~340 ms of queries), rendering still dominates.

**Decision: descope the query-fan-out refactor.** Batching (rewrite ~21 aggregator methods to multi-device
— huge surface) or bounded per-device-session concurrency (RLS-context replication + DB-pool-starvation
risk) would each carry meaningful regression risk for a **≤4%** win. And report generation is an
**asynchronous** background job (scheduled / on-demand worker), not a user-blocking request, so the absolute
latency (~1.2 s for 8 devices, ~3 s for 20) is acceptable. The measure-first step did its job: it stopped a
large, risky refactor that wouldn't have moved the needle.

**Logged follow-up (only if report latency ever becomes a problem at large fleet scale):** the lever is the
**rendering**, not the queries — e.g. process-pool parallelism for the per-device SVG rendering, or cheaper
chart generation. Out of scope here (larger effort, marginal value while report gen is async).

## Invariants

- Behavior-preserving: same enqueued jobs, same query results, same rendered reports. No API/schema/contract
  change (PR2's migration is additive indexes only).
- RLS, secrets-at-rest, SSRF, fail-closed config all untouched. The singleton pool carries no tenant data;
  it is a connection resource only.

## Testing

- **PR1:** a unit/integration test that `enqueue` reuses the same pool across calls (patch `create_pool` to
  count creations → exactly 1 for N enqueues) and that `close_pool()` resets it; the existing job-enqueue
  assertions (capturing override) still pass; full suite green.
- **PR2:** the migration applies + downgrade-not-needed (forward-only); a test asserting each new index
  exists; queries still return identical rows.
- **PR3:** descoped (no code) — the measurement (4% queries / 96% rendering) is recorded in the PR3
  section above; nothing to test.

## Out of scope (other sub-projects)

Frontend bundle, large-file splits. The Redis-backed sliding-window limiter (multi-worker) stays deferred
(only matters at multi-worker scale).

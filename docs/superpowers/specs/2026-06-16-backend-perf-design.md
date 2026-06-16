# Backend Performance — Design Spec

**Date:** 2026-06-16
**Status:** Approved (design); writing the implementation plan next.
**Milestone:** Performance + refactor — **sub-project 2 of 4** (after the connector factory; before
frontend bundle + large-file splits). Measure-first, behavior-preserving, incremental PRs.

## Goal

Three measured backend-perf wins, each its own PR:

1. **PR1 — Singleton ARQ pool** (the flagged tech-debt).
2. **PR2 — Index audit** (measured; additive migration).
3. **PR3 — Reporting per-device query fan-out** (measured; the heaviest, decided after measuring).

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
_pool_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def _get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        async with _lock():
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
- **Concurrency:** the double-checked `asyncio.Lock` ensures concurrent first-enqueues create exactly one
  pool (no leak).

## PR2 — Index audit (measured)

Run EXPLAIN (ANALYZE) against the hot read paths on a seeded DB — the reporting rollups
(`reliability_rollup`, `config_audit_rollup`, perimeter `perimeter_top`, `health_summary`,
`alerts_in_range`, `gateway_quality`), the membership/RBAC resolution, and the device/alert list filters.
For each sequential scan on a selective filter/sort that runs per report or per request, add the missing
index in a **forward-only migration**, mirroring the existing `events` keyset index. Additive only — no
query rewrite. If the audit finds nothing missing, PR2 is a documented no-op (still valuable: it records
the indexes are adequate). Scope is bounded by what the audit actually finds.

## PR3 — Reporting per-device fan-out (measured, then decided)

Measure first: instrument a representative multi-device report and count the per-device queries + wall time.
Then pick the **behavior-preserving** remedy:

- **(a) Batch** — rewrite the per-device aggregator methods to fleet-wide `… GROUP BY device_id` queries,
  splitting results in Python (one round-trip per metric instead of one per device). Biggest win; more
  invasive (each aggregator method gains a multi-device variant). The output rows must be byte-identical to
  today's per-device path (verified by a same-report-bytes test).
- **(b) Bounded concurrency** — run the per-device blocks concurrently with a **separate session per
  device** (an `AsyncSession` is NOT concurrency-safe, so the current single-session loop cannot just be
  `gather`-ed) under a semaphore bounded by the DB pool size. Smaller code change; bounded by connections.

Recommendation deferred to the measurement: (a) if the query count dominates, (b) if the per-query latency
dominates and the fleet is small. Either way the rendered report must be identical (a golden-bytes/section
test guards it). If the measured win is marginal, PR3 may be descoped to a logged follow-up.

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
- **PR3:** a golden test that the report for a multi-device tenant renders identical sections before/after;
  full suite green.

## Out of scope (other sub-projects)

Frontend bundle, large-file splits. The Redis-backed sliding-window limiter (multi-worker) stays deferred
(only matters at multi-worker scale).

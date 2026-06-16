# Backend Perf PR1 — Singleton ARQ Pool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-call ARQ pool (`create_pool`…`aclose` on every `enqueue`) with one process-level, lazily-created, reused pool, closed at app shutdown.

**Architecture:** A module-global `ArqRedis` in `app/core/queue.py`, created on first `enqueue()` under a double-checked `asyncio.Lock`, reused thereafter, and `close_pool()`-d from the FastAPI `lifespan` shutdown. Tests override `get_enqueuer`, so they never touch the real pool; the existing `test_queue.py` (which asserted per-call close) is rewritten to assert reuse.

**Tech Stack:** Python 3.14 / arq (`create_pool`, `ArqRedis`) / FastAPI lifespan / pytest.

Spec: `docs/superpowers/specs/2026-06-16-backend-perf-design.md` (PR1 section).

---

### Task 1: Singleton pool in `queue.py` + rewritten tests (TDD)

**Files:**
- Modify: `backend/app/core/queue.py`
- Test: `backend/tests/test_queue.py` (rewrite)

- [ ] **Step 1: Rewrite the tests for the singleton behavior**

Replace the entire contents of `backend/tests/test_queue.py` with:

```python
from unittest.mock import AsyncMock

import pytest

from app.core import queue


@pytest.fixture(autouse=True)
async def _reset_pool():
    """Each test starts and ends with no shared pool (avoid leaking a mock across tests)."""
    await queue.close_pool()
    yield
    await queue.close_pool()


async def test_enqueue_reuses_a_single_pool(monkeypatch):
    """The pool is created once and reused across enqueues (no per-call create/close)."""
    pool = AsyncMock()
    create = AsyncMock(return_value=pool)
    monkeypatch.setattr(queue, "create_pool", create)

    await queue.enqueue("poll_device", "dev-1")
    await queue.enqueue("ingest_device_events", "dev-2")

    assert create.await_count == 1                       # ONE pool for both calls
    assert pool.enqueue_job.await_count == 2
    pool.aclose.assert_not_awaited()                     # NOT closed per call


async def test_enqueue_passes_args_and_defer(monkeypatch):
    from datetime import UTC, datetime
    pool = AsyncMock()
    monkeypatch.setattr(queue, "create_pool", AsyncMock(return_value=pool))
    when = datetime(2030, 1, 1, tzinfo=UTC)

    await queue.enqueue("poll_device", "dev-1")
    await queue.enqueue("send_report", "r1", defer_until=when)

    pool.enqueue_job.assert_any_await("poll_device", "dev-1")
    pool.enqueue_job.assert_any_await("send_report", "r1", _defer_until=when)


async def test_pool_survives_an_enqueue_error(monkeypatch):
    """A failing enqueue_job must NOT tear down the shared pool (the next enqueue reuses it)."""
    pool = AsyncMock()
    pool.enqueue_job.side_effect = [RuntimeError("boom"), None]
    create = AsyncMock(return_value=pool)
    monkeypatch.setattr(queue, "create_pool", create)

    with pytest.raises(RuntimeError):
        await queue.enqueue("poll_device", "dev-1")
    await queue.enqueue("poll_device", "dev-2")          # reuses the same pool

    assert create.await_count == 1
    pool.aclose.assert_not_awaited()


async def test_close_pool_aclose_and_recreates(monkeypatch):
    """close_pool() releases via aclose(); a later enqueue creates a fresh pool."""
    pool1, pool2 = AsyncMock(), AsyncMock()
    create = AsyncMock(side_effect=[pool1, pool2])
    monkeypatch.setattr(queue, "create_pool", create)

    await queue.enqueue("poll_device", "dev-1")
    await queue.close_pool()
    pool1.aclose.assert_awaited_once_with()

    await queue.enqueue("poll_device", "dev-2")
    assert create.await_count == 2                       # a new pool after close


async def test_close_pool_is_idempotent_when_never_created():
    await queue.close_pool()                             # no pool yet -> no error
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_queue.py -q`
Expected: FAIL — `AttributeError: module 'app.core.queue' has no attribute 'close_pool'`.

- [ ] **Step 3: Rewrite `queue.py` to the singleton**

```python
# backend/app/core/queue.py
import asyncio
from datetime import datetime

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None
_pool_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    # Created lazily so it binds to the running loop, not import time.
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def _get_pool() -> ArqRedis:
    """The process-wide ARQ pool, created once on first use and reused thereafter."""
    global _pool
    if _pool is None:
        async with _lock():
            if _pool is None:                            # double-checked: exactly one pool is created
                _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    return _pool


async def enqueue(name: str, *args, defer_until: datetime | None = None) -> None:
    """Enqueue an ARQ job (immediate, or deferred to `defer_until`) on the shared pool."""
    pool = await _get_pool()
    kwargs = {"_defer_until": defer_until} if defer_until is not None else {}
    await pool.enqueue_job(name, *args, **kwargs)


async def close_pool() -> None:
    """Close the shared pool (app shutdown / test teardown). Idempotent."""
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


async def get_enqueuer():
    """FastAPI dependency returning the enqueue callable (overridable in tests)."""
    return enqueue
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_queue.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/queue.py backend/tests/test_queue.py
git commit -m "perf(queue): reuse a single ARQ pool instead of opening one per enqueue"
```

### Task 2: Close the pool on app shutdown

**Files:**
- Modify: `backend/app/main.py` (the `lifespan` context manager, ~line 47-52)

- [ ] **Step 1: Wire `close_pool()` into the lifespan shutdown**

In `app/main.py`, import and call it after `yield`:

```python
from app.core.queue import close_pool   # add to the imports near the other app.core imports
...
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed at startup if any secret is still an .env.example placeholder (weak default creds).
    assert_secure_secrets(get_settings())
    yield
    await close_pool()                    # release the shared ARQ pool on graceful shutdown
```

- [ ] **Step 2: Verify the app still imports + the lifespan is valid**

Run: `cd backend && python -c "import app.main"` then `python -m pytest tests/test_health.py -q` (or any app-level test that exercises the app fixture).
Expected: imports cleanly; tests pass.

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "perf(queue): close the shared ARQ pool on app shutdown (lifespan)"
```

### Task 3: Backend gate + open PR

- [ ] **Step 1: Lint + full suite**

Run: `cd backend && ruff check app/ && python -m pytest -q`
Expected: ruff clean; all tests pass (the 7 routers' enqueue tests use the `_noop_enqueue` override and are
unaffected; `test_queue.py` is green).

- [ ] **Step 2: Push + PR**

```bash
git push -u origin perf/singleton-arq-pool
```
PR to `main`: `perf(queue): singleton ARQ pool (perf+refactor 2/4 · PR1)`. Spec link + the per-call→reused
summary. Green CI → squash-merge.

---

## Self-review (plan vs spec)

- **Spec coverage (PR1):** singleton lazy pool + double-checked lock (T1 impl) ✓; `enqueue` reuses it (T1 tests) ✓; `close_pool()` idempotent + recreates (T1 tests) ✓; lifespan shutdown wiring (T2) ✓; tests-don't-need-Redis (the `get_enqueuer` override is unchanged; `test_queue` patches `create_pool`) ✓; full-suite green (T3) ✓.
- **Placeholder scan:** none — full code in every step; the test file is given in full.
- **Type/name consistency:** `_pool`/`_pool_lock`/`_get_pool`/`enqueue`/`close_pool`/`get_enqueuer` used identically across impl, tests, and the lifespan; `ArqRedis`/`create_pool`/`RedisSettings` imports match `arq.connections`.

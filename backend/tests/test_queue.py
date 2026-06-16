import asyncio
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


async def test_concurrent_first_enqueues_create_exactly_one_pool(monkeypatch):
    """Under CONCURRENT first-enqueues the lock must serialize creation to a single pool. `create_pool`
    yields control after the `_pool is None` check so a broken/missing lock would create several pools."""
    pool = AsyncMock()

    async def slow_create(*_a, **_k):
        await asyncio.sleep(0)                            # let the other gathered tasks run
        return pool

    create = AsyncMock(side_effect=slow_create)
    monkeypatch.setattr(queue, "create_pool", create)

    await asyncio.gather(*(queue.enqueue("poll_device", f"dev-{i}") for i in range(8)))

    assert create.await_count == 1                       # exactly one pool despite 8 concurrent callers
    assert pool.enqueue_job.await_count == 8


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

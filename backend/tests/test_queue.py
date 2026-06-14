import contextlib
from unittest.mock import AsyncMock

from app.core import queue


async def test_enqueue_closes_pool_with_aclose(monkeypatch):
    """enqueue() must release the per-call pool via aclose() (redis>=5 API)."""
    pool = AsyncMock()
    monkeypatch.setattr(queue, "create_pool", AsyncMock(return_value=pool))

    await queue.enqueue("poll_device", "dev-1")

    pool.enqueue_job.assert_awaited_once_with("poll_device", "dev-1")
    pool.aclose.assert_awaited_once_with()
    assert not pool.close.await_count  # the deprecated close() is no longer used


async def test_enqueue_closes_pool_even_on_error(monkeypatch):
    """The pool is released even when enqueue_job raises."""
    pool = AsyncMock()
    pool.enqueue_job.side_effect = RuntimeError("boom")
    monkeypatch.setattr(queue, "create_pool", AsyncMock(return_value=pool))

    with contextlib.suppress(RuntimeError):
        await queue.enqueue("poll_device", "dev-1")

    pool.aclose.assert_awaited_once_with()

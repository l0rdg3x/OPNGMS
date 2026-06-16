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

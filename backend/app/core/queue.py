import asyncio
from datetime import datetime

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

_pool: ArqRedis | None = None
# Eager module-level lock: since Python 3.10 asyncio.Lock binds to the running loop on first acquire
# (not at construction), so this is import-safe AND avoids a TOCTOU race a lazily-built lock would have
# (two first-callers each building their own lock -> two pools). One lock guards every (re)creation.
_pool_lock = asyncio.Lock()


async def _get_pool() -> ArqRedis:
    """The process-wide ARQ pool, created once on first use and reused thereafter."""
    global _pool
    if _pool is None:
        async with _pool_lock:
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

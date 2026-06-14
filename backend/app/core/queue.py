from datetime import datetime

from arq import create_pool
from arq.connections import RedisSettings

from app.core.config import get_settings


async def enqueue(name: str, *args, defer_until: datetime | None = None) -> None:
    """Enqueue an ARQ job (immediate, or deferred to `defer_until`).

    Opens one pool per call (low volume); see technical debt for a singleton pool.
    """
    pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url))
    try:
        kwargs = {"_defer_until": defer_until} if defer_until is not None else {}
        await pool.enqueue_job(name, *args, **kwargs)
    finally:
        await pool.aclose()


async def get_enqueuer():
    """FastAPI dependency returning the enqueue callable (overridable in tests)."""
    return enqueue

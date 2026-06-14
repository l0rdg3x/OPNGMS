import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def make_engine(url: str) -> AsyncEngine:
    s = get_settings()
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
    )


_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _factory


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Set app.current_tenant for the current transaction (drives RLS)."""
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def reset_tenant_context(session: AsyncSession) -> None:
    """Clear app.current_tenant back to the fail-closed neutral state ('' -> NULL -> no rows).

    Used after a loop that sets the context for several tenants in turn (e.g. the superadmin
    impacted-tenants scan), so a later query on the same session can't inherit the last tenant's context.
    """
    await session.execute(text("SELECT set_config('app.current_tenant', '', true)"))


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session

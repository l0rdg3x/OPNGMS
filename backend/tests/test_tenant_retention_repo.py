import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.tenant_retention import TenantRetentionRepository
from tests.factories import make_tenant


@pytest.fixture
def sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_upsert_and_clear(sf):
    async with sf() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    async with sf() as s:
        await set_tenant_context(s, tid)
        repo = TenantRetentionRepository(s, tid)
        assert await repo.get_overrides() == {}
        await repo.upsert({"perimeter": 7, "events": 14})
        await s.commit()
    async with sf() as s:
        await set_tenant_context(s, tid)
        repo = TenantRetentionRepository(s, tid)
        assert await repo.get_overrides() == {"perimeter": 7, "events": 14}
        await repo.upsert({"perimeter": None, "metrics": 5})  # None clears perimeter
        await s.commit()
    async with sf() as s:
        await set_tenant_context(s, tid)
        assert await TenantRetentionRepository(s, tid).get_overrides() == {"events": 14, "metrics": 5}

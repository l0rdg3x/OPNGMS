import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, tenant: Tenant) -> Tenant:
        self.session.add(tenant)
        await self.session.flush()
        return tenant

    async def get(self, tenant_id: uuid.UUID) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def list(self) -> list[Tenant]:
        result = await self.session.execute(select(Tenant).order_by(Tenant.slug))
        return list(result.scalars().all())

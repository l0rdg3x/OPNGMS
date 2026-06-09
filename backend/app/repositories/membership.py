import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import Membership


class MembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, membership: Membership) -> Membership:
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Membership]:
        result = await self.session.execute(
            select(Membership).where(Membership.tenant_id == tenant_id)
        )
        return list(result.scalars().all())

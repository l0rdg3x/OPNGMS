import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group, GroupGrant, GroupMember


class GroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self) -> list[Group]:
        return list((await self.session.execute(select(Group).order_by(Group.name))).scalars().all())

    async def get(self, group_id: uuid.UUID) -> Group | None:
        return await self.session.get(Group, group_id)

    async def add(self, group: Group) -> Group:
        self.session.add(group)
        await self.session.flush()
        return group

    async def delete(self, group: Group) -> None:
        await self.session.delete(group)
        await self.session.flush()

    async def member_ids(self, group_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            (
                await self.session.execute(
                    select(GroupMember.user_id).where(GroupMember.group_id == group_id)
                )
            )
            .scalars()
            .all()
        )

    async def set_members(self, group_id: uuid.UUID, user_ids: list[uuid.UUID]) -> None:
        """Replace the group's membership with exactly `user_ids` (deduplicated)."""
        await self.session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
        for uid in dict.fromkeys(user_ids):  # dedupe, preserve order
            self.session.add(GroupMember(group_id=group_id, user_id=uid))
        await self.session.flush()

    async def grants(self, group_id: uuid.UUID) -> list[GroupGrant]:
        return list(
            (
                await self.session.execute(
                    select(GroupGrant).where(GroupGrant.group_id == group_id).order_by(GroupGrant.id)
                )
            )
            .scalars()
            .all()
        )

    async def add_grant(
        self, group_id: uuid.UUID, *, all_tenants: bool, tenant_id: uuid.UUID | None, role: str
    ) -> GroupGrant:
        grant = GroupGrant(
            group_id=group_id, all_tenants=all_tenants, tenant_id=tenant_id, role=role
        )
        self.session.add(grant)
        await self.session.flush()
        return grant

    async def get_grant(self, grant_id: uuid.UUID) -> GroupGrant | None:
        return await self.session.get(GroupGrant, grant_id)

    async def delete_grant(self, grant: GroupGrant) -> None:
        await self.session.delete(grant)
        await self.session.flush()

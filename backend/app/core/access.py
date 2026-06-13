"""Effective tenant access resolution: direct Membership + group grants (highest role wins).

Group grants only ever carry one of the three tenant roles and only widen which tenants a user may
ENTER — never the RLS scope once inside a tenant. Superadmin is handled by `can()`, not here.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rbac import highest_role
from app.models.group import GroupGrant, GroupMember
from app.models.membership import Membership
from app.models.tenant import Tenant
from app.models.user import User


async def resolve_effective_role(
    session: AsyncSession, *, user: User, tenant_id: uuid.UUID
) -> str | None:
    """The most-privileged tenant role this (non-superadmin) user has on `tenant_id`, else None.

    Union of the direct membership role and every group-grant role whose scope covers the tenant
    (a wildcard `all_tenants` grant or a grant for exactly this tenant)."""
    roles: list[str | None] = []
    roles.extend(
        (
            await session.execute(
                select(Membership.role).where(
                    Membership.user_id == user.id, Membership.tenant_id == tenant_id
                )
            )
        )
        .scalars()
        .all()
    )
    roles.extend(
        (
            await session.execute(
                select(GroupGrant.role)
                .join(GroupMember, GroupMember.group_id == GroupGrant.group_id)
                .where(
                    GroupMember.user_id == user.id,
                    GroupGrant.all_tenants.is_(True) | (GroupGrant.tenant_id == tenant_id),
                )
            )
        )
        .scalars()
        .all()
    )
    return highest_role(roles)


async def tenants_for_user(session: AsyncSession, user: User) -> dict[uuid.UUID, str]:
    """Map of every tenant this (non-superadmin) user can reach -> effective (highest) role.

    Direct memberships + group grants; a wildcard grant expands to every tenant."""
    result: dict[uuid.UUID, str] = {}

    def _bump(tid: uuid.UUID, role: str) -> None:
        merged = highest_role([result.get(tid), role])
        if merged is not None:
            result[tid] = merged

    for tid, role in (
        await session.execute(
            select(Membership.tenant_id, Membership.role).where(Membership.user_id == user.id)
        )
    ).all():
        _bump(tid, role)

    grants = (
        await session.execute(
            select(GroupGrant.all_tenants, GroupGrant.tenant_id, GroupGrant.role)
            .join(GroupMember, GroupMember.group_id == GroupGrant.group_id)
            .where(GroupMember.user_id == user.id)
        )
    ).all()
    wildcard_roles = [g.role for g in grants if g.all_tenants]
    if wildcard_roles:
        all_tids = (await session.execute(select(Tenant.id))).scalars().all()
        for role in wildcard_roles:
            for tid in all_tids:
                _bump(tid, role)
    for g in grants:
        if not g.all_tenants and g.tenant_id is not None:
            _bump(g.tenant_id, g.role)
    return result

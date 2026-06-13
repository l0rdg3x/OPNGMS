import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.group import Group
from app.models.user import User
from app.repositories.group import GroupRepository
from app.schemas.group import (
    GroupGrantIn,
    GroupGrantOut,
    GroupIn,
    GroupMembersIn,
    GroupOut,
    GroupUpdateIn,
)
from app.services.audit import AuditService

router = APIRouter(prefix="/api/groups", tags=["groups"])


async def _serialize(repo: GroupRepository, group: Group) -> GroupOut:
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        member_ids=await repo.member_ids(group.id),
        grants=[GroupGrantOut.model_validate(g) for g in await repo.grants(group.id)],
    )


async def _group_or_404(repo: GroupRepository, group_id: uuid.UUID) -> Group:
    group = await repo.get(group_id)
    if group is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    return group


@router.get("", response_model=list[GroupOut])
async def list_groups(
    user: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[GroupOut]:
    repo = GroupRepository(session)
    return [await _serialize(repo, g) for g in await repo.list()]


@router.post(
    "", response_model=GroupOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_group(
    payload: GroupIn,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    repo = GroupRepository(session)
    group = await repo.add(Group(name=payload.name, description=payload.description))
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.create",
        target_type="group", target_id=str(group.id),
        ip=request.client.host if request.client else None,
        details={"name": group.name},
    )
    await session.commit()
    return await _serialize(repo, group)


@router.patch(
    "/{group_id}", response_model=GroupOut, dependencies=[Depends(enforce_csrf)],
)
async def update_group(
    group_id: uuid.UUID,
    payload: GroupUpdateIn,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    repo = GroupRepository(session)
    group = await _group_or_404(repo, group_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(group, field, value)
    await session.flush()
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.update",
        target_type="group", target_id=str(group.id),
        ip=request.client.host if request.client else None, details=changes,
    )
    await session.commit()
    return await _serialize(repo, group)


@router.delete(
    "/{group_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_group(
    group_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = GroupRepository(session)
    group = await _group_or_404(repo, group_id)
    await repo.delete(group)
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.delete",
        target_type="group", target_id=str(group_id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()


@router.put(
    "/{group_id}/members", response_model=GroupOut, dependencies=[Depends(enforce_csrf)],
)
async def set_members(
    group_id: uuid.UUID,
    payload: GroupMembersIn,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    repo = GroupRepository(session)
    group = await _group_or_404(repo, group_id)
    try:
        await repo.set_members(group_id, payload.user_ids)
    except IntegrityError as exc:  # unknown user id -> FK violation
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unknown user id") from exc
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.set_members",
        target_type="group", target_id=str(group.id),
        ip=request.client.host if request.client else None,
        details={"count": len(set(payload.user_ids))},
    )
    await session.commit()
    return await _serialize(repo, group)


@router.post(
    "/{group_id}/grants", response_model=GroupGrantOut,
    status_code=status.HTTP_201_CREATED, dependencies=[Depends(enforce_csrf)],
)
async def add_grant(
    group_id: uuid.UUID,
    payload: GroupGrantIn,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> GroupGrantOut:
    repo = GroupRepository(session)
    await _group_or_404(repo, group_id)
    try:
        grant = await repo.add_grant(
            group_id, all_tenants=payload.all_tenants, tenant_id=payload.tenant_id, role=payload.role
        )
    except IntegrityError as exc:  # duplicate scope (partial unique) or unknown tenant (FK)
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A grant for this scope already exists, or the tenant is unknown",
        ) from exc
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.add_grant",
        target_type="group", target_id=str(group_id),
        ip=request.client.host if request.client else None,
        details={"all_tenants": payload.all_tenants, "tenant_id": str(payload.tenant_id) if payload.tenant_id else None, "role": payload.role},
    )
    await session.commit()
    return GroupGrantOut.model_validate(grant)


@router.delete(
    "/{group_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_grant(
    group_id: uuid.UUID,
    grant_id: uuid.UUID,
    request: Request,
    actor: User = Depends(require_org(Action.GROUP_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = GroupRepository(session)
    await _group_or_404(repo, group_id)
    grant = await repo.get_grant(grant_id)
    if grant is None or grant.group_id != group_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found")
    await repo.delete_grant(grant)
    await AuditService(session).record(
        actor_user_id=actor.id, tenant_id=None, action="group.delete_grant",
        target_type="group", target_id=str(group_id),
        ip=request.client.host if request.client else None, details={"grant_id": str(grant_id)},
    )
    await session.commit()

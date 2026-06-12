import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import (
    TenantContext,
    enforce_csrf,
    get_current_user,
    require_org,
    require_tenant,
)
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.config_profile import ConfigProfile, ConfigProfileMember
from app.models.config_template import ConfigTemplate
from app.models.device import Device
from app.models.user import User
from app.schemas.profiles import (
    ApplyProfileIn,
    ProfileApplyOut,
    ProfileIn,
    ProfileOut,
    ProfileUpdateIn,
)
from app.schemas.templates import PreviewTemplateIn, TemplatePreviewOut
from app.services.audit import AuditService
from app.services.profiles import _effective, _ordered_members, materialize_profile
from app.services.templates import (
    TEMPLATE_KINDS,
    InvalidTemplateError,
    apply_bindings,
    validate_body,
)

router = APIRouter(prefix="/api", tags=["profiles"])


# ---------- helpers ----------


async def _member_template_ids(session: AsyncSession, profile_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await session.execute(
            select(ConfigProfileMember.template_id)
            .where(ConfigProfileMember.profile_id == profile_id)
            .order_by(ConfigProfileMember.position, ConfigProfileMember.id)
        )
    ).scalars().all()
    return list(rows)


async def _set_members(
    session: AsyncSession, profile_id: uuid.UUID, template_ids: list[uuid.UUID]
) -> None:
    """Validate each template exists, then insert ordered members (position = index)."""
    for index, tpl_id in enumerate(template_ids):
        tpl = await session.get(ConfigTemplate, tpl_id)
        if tpl is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Template not found: {tpl_id}",
            )
        session.add(
            ConfigProfileMember(profile_id=profile_id, template_id=tpl_id, position=index)
        )


def _profile_out(profile: ConfigProfile, template_ids: list[uuid.UUID]) -> ProfileOut:
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        version=profile.version,
        template_ids=template_ids,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def _device_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID
) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


async def _profile_or_404(session: AsyncSession, profile_id: uuid.UUID) -> ConfigProfile:
    profile = await session.get(ConfigProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
    return profile


# ---------- global library (superadmin-managed) ----------


@router.post(
    "/profiles",
    response_model=ProfileOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_profile(
    body: ProfileIn,
    request: Request,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    profile = ConfigProfile(
        name=body.name,
        description=body.description,
        created_by=user.id,
    )
    session.add(profile)
    await session.flush()
    await _set_members(session, profile.id, body.template_ids)
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="profile.create",
        target_type="config_profile",
        target_id=str(profile.id),
        ip=request.client.host if request.client else None,
        details={"name": profile.name, "members": len(body.template_ids)},
    )
    await session.commit()
    await session.refresh(profile)
    return _profile_out(profile, list(body.template_ids))


@router.get("/profiles", response_model=list[ProfileOut])
async def list_profiles(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ProfileOut]:
    # Any authenticated user may read the global profile library (needed to apply). It lives at
    # /api/profiles with NO tenant_id in the path, so this uses get_current_user (not require_tenant);
    # profiles are global, no tenant RLS to satisfy.
    profiles = (
        await session.execute(select(ConfigProfile).order_by(ConfigProfile.name))
    ).scalars().all()
    out: list[ProfileOut] = []
    for profile in profiles:
        template_ids = await _member_template_ids(session, profile.id)
        out.append(_profile_out(profile, template_ids))
    return out


@router.put(
    "/profiles/{profile_id}",
    response_model=ProfileOut,
    dependencies=[Depends(enforce_csrf)],
)
async def update_profile(
    profile_id: uuid.UUID,
    body: ProfileUpdateIn,
    request: Request,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> ProfileOut:
    profile = await _profile_or_404(session, profile_id)
    if body.name is not None:
        profile.name = body.name
    if body.description is not None:
        profile.description = body.description
    if body.template_ids is not None:
        # REPLACE the ordered member set: delete existing, re-insert in order.
        await session.execute(
            delete(ConfigProfileMember).where(ConfigProfileMember.profile_id == profile.id)
        )
        await session.flush()
        await _set_members(session, profile.id, body.template_ids)
    profile.version += 1
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="profile.update",
        target_type="config_profile",
        target_id=str(profile.id),
        ip=request.client.host if request.client else None,
        details={"version": profile.version},
    )
    await session.commit()
    await session.refresh(profile)
    template_ids = await _member_template_ids(session, profile.id)
    return _profile_out(profile, template_ids)


@router.delete(
    "/profiles/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_profile(
    profile_id: uuid.UUID,
    request: Request,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    profile = await session.get(ConfigProfile, profile_id)
    # Idempotent: deleting a non-existent profile is a no-op (204). Members CASCADE.
    if profile is not None:
        pid = str(profile.id)
        await session.delete(profile)
        await AuditService(session).record(
            actor_user_id=user.id,
            tenant_id=None,
            action="profile.delete",
            target_type="config_profile",
            target_id=pid,
            ip=request.client.host if request.client else None,
            details={},
        )
        await session.commit()


# ---------- apply / preview (tenant) ----------


@router.post(
    "/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/preview",
    response_model=list[TemplatePreviewOut],
    dependencies=[Depends(enforce_csrf)],
)
async def preview_profile(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: PreviewTemplateIn | None = None,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> list[TemplatePreviewOut]:
    await _device_or_404(session, tenant_id, device_id)
    await _profile_or_404(session, profile_id)
    templates = await _ordered_members(session, profile_id)
    if not templates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Profile has no member templates"
        )
    binds = body.bindings if body else {}
    previews: list[TemplatePreviewOut] = []
    for tpl in templates:
        eff = await _effective(session, tenant_id, tpl)
        # Apply the per-kind apply-time binding (e.g. firewall_rule interface) so the preview
        # reflects what apply will materialize; identity for kinds without a bind hook.
        eff = apply_bindings(tpl.kind, eff, binds)
        try:
            validate_body(tpl.kind, eff)
        except InvalidTemplateError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
            ) from exc
        # Derive operation/target/kind from each member's kind via the registry mapping
        # (kind-aware: alias uses name, opnsense_setting uses endpoint_key, etc.).
        spec = TEMPLATE_KINDS[tpl.kind]
        op, target, _ = spec.to_change(eff)
        previews.append(
            TemplatePreviewOut(operation=op, kind=spec.change_kind, target=str(target), new=eff)
        )
    return previews


@router.post(
    "/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/apply",
    response_model=ProfileApplyOut,
    dependencies=[Depends(enforce_csrf)],
)
async def apply_profile(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    profile_id: uuid.UUID,
    body: ApplyProfileIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> ProfileApplyOut:
    await _device_or_404(session, tenant_id, device_id)
    profile = await _profile_or_404(session, profile_id)
    try:
        changes = await materialize_profile(
            session,
            tenant_id=tenant_id,
            device_id=device_id,
            created_by=ctx.user.id,
            profile=profile,
            bindings=body.bindings,
        )
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    for change in changes:
        change.status = "scheduled"
        change.scheduled_at = body.scheduled_at
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="profile.apply",
        target_type="config_profile",
        target_id=str(profile.id),
        ip=request.client.host if request.client else None,
        details={"profile_id": str(profile.id), "count": len(changes)},
    )
    await session.commit()
    # ONE job for the whole profile: members are applied in order under a single device lock so a
    # member that mutates config.xml can't make its siblings falsely conflict (the per-member fan-out
    # used to do exactly that).
    await enqueue("apply_profile_changes", [str(c.id) for c in changes], defer_until=body.scheduled_at)
    return ProfileApplyOut(change_ids=[c.id for c in changes], status="scheduled")

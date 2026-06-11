import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
from app.models.config_template import ConfigTemplate
from app.models.device import Device
from app.models.template_override import TemplateOverride
from app.models.user import User
from app.schemas.templates import (
    ApplyTemplateIn,
    OverrideIn,
    OverrideOut,
    TemplateIn,
    TemplateOut,
    TemplatePreviewOut,
    TemplateUpdateIn,
)
from app.services.audit import AuditService
from app.services.templates import (
    InvalidTemplateError,
    effective_body,
    materialize_change,
    validate_body,
)

router = APIRouter(prefix="/api", tags=["templates"])


# ---------- global library (superadmin-managed) ----------


@router.post(
    "/templates",
    response_model=TemplateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_csrf)],
)
async def create_template(
    body: TemplateIn,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    try:
        validate_body(body.kind, body.body)
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    tpl = ConfigTemplate(
        kind=body.kind,
        name=body.name,
        description=body.description,
        body=body.body,
        created_by=user.id,
    )
    session.add(tpl)
    await session.flush()
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="template.create",
        target_type="config_template",
        target_id=str(tpl.id),
        ip=None,
        details={"kind": tpl.kind, "name": tpl.name},
    )
    await session.commit()
    await session.refresh(tpl)
    return TemplateOut.model_validate(tpl)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TemplateOut]:
    # Any authenticated user may read the global library (needed to apply). It lives at /api/templates
    # with NO tenant_id in the path, so this uses get_current_user (not require_tenant, which binds a
    # tenant from the path) — the library is global, no tenant RLS to satisfy.
    rows = (
        await session.execute(
            select(ConfigTemplate).order_by(ConfigTemplate.kind, ConfigTemplate.name)
        )
    ).scalars().all()
    return [TemplateOut.model_validate(r) for r in rows]


@router.put(
    "/templates/{template_id}",
    response_model=TemplateOut,
    dependencies=[Depends(enforce_csrf)],
)
async def update_template(
    template_id: uuid.UUID,
    body: TemplateUpdateIn,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    tpl = await session.get(ConfigTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    if body.body is not None:
        try:
            validate_body(tpl.kind, body.body)
        except InvalidTemplateError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        tpl.body = body.body
    if body.name is not None:
        tpl.name = body.name
    if body.description is not None:
        tpl.description = body.description
    tpl.version += 1
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="template.update",
        target_type="config_template",
        target_id=str(tpl.id),
        ip=None,
        details={"version": tpl.version},
    )
    await session.commit()
    await session.refresh(tpl)
    return TemplateOut.model_validate(tpl)


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(enforce_csrf)],
)
async def delete_template(
    template_id: uuid.UUID,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    tpl = await session.get(ConfigTemplate, template_id)
    # Idempotent: deleting a non-existent template is a no-op (204).
    if tpl is not None:
        tid = str(tpl.id)
        await session.delete(tpl)
        await AuditService(session).record(
            actor_user_id=user.id,
            tenant_id=None,
            action="template.delete",
            target_type="config_template",
            target_id=tid,
            ip=None,
            details={},
        )
        await session.commit()


# ---------- per-tenant override ----------


async def _template_or_404(session: AsyncSession, template_id: uuid.UUID) -> ConfigTemplate:
    tpl = await session.get(ConfigTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    return tpl


@router.put(
    "/tenants/{tenant_id}/templates/{template_id}/override",
    response_model=OverrideOut,
    dependencies=[Depends(enforce_csrf)],
)
async def upsert_override(
    tenant_id: uuid.UUID,
    template_id: uuid.UUID,
    body: OverrideIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> OverrideOut:
    await _template_or_404(session, template_id)
    existing = (
        await session.execute(
            select(TemplateOverride).where(
                TemplateOverride.template_id == template_id,
                TemplateOverride.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = TemplateOverride(
            template_id=template_id, tenant_id=tenant_id, body_patch=body.body_patch
        )
        session.add(existing)
    else:
        existing.body_patch = body.body_patch
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="template.override",
        target_type="template_override",
        target_id=str(template_id),
        ip=None,
        details={},
    )
    await session.commit()
    await session.refresh(existing)
    return OverrideOut.model_validate(existing)


async def _effective(session: AsyncSession, tenant_id: uuid.UUID, tpl: ConfigTemplate) -> dict:
    ov = (
        await session.execute(
            select(TemplateOverride).where(
                TemplateOverride.template_id == tpl.id,
                TemplateOverride.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    return effective_body(tpl.kind, tpl.body, ov.body_patch if ov else {})


# ---------- apply / preview (tenant) ----------


async def _device_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID
) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.post(
    "/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview",
    response_model=TemplatePreviewOut,
    dependencies=[Depends(enforce_csrf)],
)
async def preview_template(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    template_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> TemplatePreviewOut:
    await _device_or_404(session, tenant_id, device_id)
    tpl = await _template_or_404(session, template_id)
    eff = await _effective(session, tenant_id, tpl)
    try:
        validate_body(tpl.kind, eff)
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    # M1: firewall_alias maps to the config-push 'alias' kind
    return TemplatePreviewOut(operation="set", kind="alias", target=eff["name"], new=eff)


@router.post(
    "/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/apply",
    dependencies=[Depends(enforce_csrf)],
)
async def apply_template(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    template_id: uuid.UUID,
    body: ApplyTemplateIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> dict:
    await _device_or_404(session, tenant_id, device_id)
    tpl = await _template_or_404(session, template_id)
    eff = await _effective(session, tenant_id, tpl)
    try:
        change = await materialize_change(
            session,
            tenant_id=tenant_id,
            device_id=device_id,
            created_by=ctx.user.id,
            template_id=template_id,
            kind=tpl.kind,
            body=eff,
        )
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    change.status = "scheduled"
    change.scheduled_at = body.scheduled_at
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="template.apply",
        target_type="config_change",
        target_id=str(change.id),
        ip=None,
        details={"template_id": str(template_id), "status": "scheduled"},
    )
    await session.commit()
    await enqueue("apply_config_change", str(change.id), defer_until=body.scheduled_at)
    return {"change_id": str(change.id), "status": "scheduled"}

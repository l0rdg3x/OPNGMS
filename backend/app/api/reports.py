import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.repositories.report_settings import ReportSettingsRepository
from app.schemas.report import ReportRequest
from app.schemas.report_settings import ReportSettingsIn, ReportSettingsOut
from app.services.audit import AuditService
from app.services.reporting.service import (
    MAX_LOGO_BYTES,
    ReportRangeError,
    ReportService,
    validate_logo,
)

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["reports"])


def _settings_to_out(settings) -> ReportSettingsOut:
    return ReportSettingsOut(
        title=settings.title,
        owner=settings.owner,
        timezone=settings.timezone,
        has_logo=settings.logo is not None,
        logo_mime=settings.logo_mime,
    )


@router.post("/reports", dependencies=[Depends(enforce_csrf)])
async def generate_report(
    tenant_id: uuid.UUID,
    payload: ReportRequest,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_GENERATE)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        pdf = await ReportService(session, tenant_id).build_report(
            tenant_name=ctx.tenant.name,
            frm=payload.from_,
            to=payload.to,
        )
    except ReportRangeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.generate",
        target_type="report",
        target_id=None,
        ip=request.client.host if request.client else None,
        details={"from": payload.from_.isoformat(), "to": payload.to.isoformat()},
    )
    await session.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="opngms-report.pdf"'},
    )


@router.get("/reports/settings")
async def get_report_settings(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> ReportSettingsOut:
    repo = ReportSettingsRepository(session, tenant_id)
    settings = await repo.get_or_default()
    return _settings_to_out(settings)


@router.put("/reports/settings", dependencies=[Depends(enforce_csrf)])
async def update_report_settings(
    tenant_id: uuid.UUID,
    body: ReportSettingsIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> ReportSettingsOut:
    repo = ReportSettingsRepository(session, tenant_id)
    settings = await repo.upsert(title=body.title, owner=body.owner, timezone=body.timezone)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.settings.update",
        target_type="report_settings",
        target_id=str(tenant_id),
        ip=request.client.host if request.client else None,
        details={"title": body.title, "owner": body.owner, "timezone": body.timezone},
    )
    # Capture response within the same transaction before commit (keeps RLS context active).
    out = _settings_to_out(settings)
    await session.commit()
    return out


@router.put("/reports/settings/logo", dependencies=[Depends(enforce_csrf)])
async def upload_report_logo(
    tenant_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> ReportSettingsOut:
    # Reject oversized uploads early (Starlette populates `.size` from the spooled body) before
    # buffering it all into a bytes object; validate_logo re-checks the actual length too.
    if file.size is not None and file.size > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="logo too large (max 512 KB)"
        )
    data = await file.read()
    # CRITICAL: derive mime from magic bytes — NEVER trust file.content_type
    try:
        mime = validate_logo(data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    repo = ReportSettingsRepository(session, tenant_id)
    await repo.set_logo(data, mime)
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.settings.logo",
        target_type="report_settings",
        target_id=str(tenant_id),
        ip=request.client.host if request.client else None,
        details={"mime": mime, "size": len(data)},
    )
    # Read back within the same transaction (before commit) so RLS context is still active.
    settings = await repo.get_or_default()
    out = _settings_to_out(settings)
    await session.commit()
    return out


@router.delete("/reports/settings/logo", dependencies=[Depends(enforce_csrf)])
async def delete_report_logo(
    tenant_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> ReportSettingsOut:
    repo = ReportSettingsRepository(session, tenant_id)
    await repo.clear_logo()
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.settings.logo.clear",
        target_type="report_settings",
        target_id=str(tenant_id),
        ip=request.client.host if request.client else None,
        details={},
    )
    # Read back within the same transaction before commit (RLS context still active).
    settings = await repo.get_or_default()
    out = _settings_to_out(settings)
    await session.commit()
    return out


@router.get("/reports/settings/logo")
async def get_report_logo(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    repo = ReportSettingsRepository(session, tenant_id)
    settings = await repo.get_or_default()
    if settings.logo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No logo configured")
    return Response(content=settings.logo, media_type=settings.logo_mime)

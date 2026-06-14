import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.repositories.generated_report import GeneratedReportRepository
from app.repositories.report_settings import ReportSettingsRepository
from app.schemas.generated_report import GeneratedReportOut
from app.schemas.report import ReportRequest
from app.schemas.report_settings import ReportLanguageOut, ReportSettingsIn, ReportSettingsOut
from app.services.audit import AuditService
from app.services.report_retention import limiting_store_for_sections
from app.services.reporting.i18n import REPORT_LOCALES, available_locales
from app.services.reporting.sections import resolve_sections
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
        language=settings.language,
        from_email=settings.from_email,
        sections=dict(settings.sections or {}),
    )


@router.post("/reports", dependencies=[Depends(enforce_csrf)])
async def generate_report(
    tenant_id: uuid.UUID,
    payload: ReportRequest,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_GENERATE)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Report-side retention BLOCK (SP-1 PR4a): refuse a range wider than the tenant's effective retention
    # for the stores its enabled sections read — else it would request already-purged data. Bound from the
    # tenant's report-settings sections (the on-demand report uses those; no per-schedule override here).
    settings = await ReportSettingsRepository(session, tenant_id).get_or_default()
    enabled = resolve_sections(settings.sections, None)
    limiting = await limiting_store_for_sections(session, tenant_id, enabled)
    if limiting is not None and (payload.to - payload.from_).days > limiting[1]:
        store, days = limiting
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"report range exceeds {store} retention ({days} days)",
        )
    try:
        pdf = await ReportService(session, tenant_id).build_report(
            tenant_name=ctx.tenant.name,
            frm=payload.from_,
            to=payload.to,
        )
        await GeneratedReportRepository(session, tenant_id).create(
            kind="on_demand",
            period_from=payload.from_,
            period_to=payload.to,
            created_by=ctx.user.id,
            pdf=pdf,
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


@router.get("/reports", response_model=list[GeneratedReportOut])
async def list_generated_reports(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[GeneratedReportOut]:
    rows = await GeneratedReportRepository(session, tenant_id).list()
    return [
        GeneratedReportOut(
            id=r.id,
            kind=r.kind,
            period_from=r.period_from,
            period_to=r.period_to,
            created_by=r.created_by,
            size=r.size,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/reports/{report_id}/download")
async def download_generated_report(
    tenant_id: uuid.UUID,
    report_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> Response:
    row = await GeneratedReportRepository(session, tenant_id).get(report_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return Response(
        content=row.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="report-{report_id}.pdf"'},
    )


@router.get("/reports/languages", response_model=list[ReportLanguageOut])
async def get_report_languages(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
) -> list[ReportLanguageOut]:
    return [ReportLanguageOut(code=c, name=n) for c, n in available_locales()]


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
    if body.language not in REPORT_LOCALES:
        raise HTTPException(status_code=400, detail="unsupported language")
    if body.from_email:
        from email_validator import EmailNotValidError, validate_email
        try:
            validate_email(body.from_email, check_deliverability=False)
        except EmailNotValidError as exc:
            raise HTTPException(status_code=400, detail="invalid from_email") from exc
    repo = ReportSettingsRepository(session, tenant_id)
    settings = await repo.upsert(
        title=body.title, owner=body.owner, timezone=body.timezone, language=body.language,
        from_email=body.from_email, sections=body.sections,
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id,
        tenant_id=tenant_id,
        action="report.settings.update",
        target_type="report_settings",
        target_id=str(tenant_id),
        ip=request.client.host if request.client else None,
        details={
            "title": body.title,
            "owner": body.owner,
            "timezone": body.timezone,
            "language": body.language,
            "from_email": body.from_email,
        },
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

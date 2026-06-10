import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.schemas.report import ReportRequest
from app.services.audit import AuditService
from app.services.reporting.service import ReportRangeError, ReportService

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["reports"])


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

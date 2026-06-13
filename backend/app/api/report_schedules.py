import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.report_schedule import ReportScheduleRepository
from app.schemas.report_schedule import ReportScheduleIn, ReportScheduleOut
from app.services.audit import AuditService
from app.services.report_schedule import FREQUENCIES, WEEKLY, normalize_recipients

router = APIRouter(prefix="/api/tenants/{tenant_id}/report-schedules", tags=["report-schedules"])


def _out(row) -> ReportScheduleOut:
    return ReportScheduleOut(
        id=row.id, device_id=row.device_id, enabled=row.enabled, frequency=row.frequency,
        weekday=row.weekday, hour=row.hour, recipients=list(row.recipients or []),
        sections=row.sections, next_run_at=row.next_run_at, last_run_at=row.last_run_at,
    )


@router.get("", response_model=list[ReportScheduleOut])
async def list_schedules(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ReportScheduleOut]:
    return [_out(r) for r in await ReportScheduleRepository(session, tenant_id).list()]


@router.put("", response_model=ReportScheduleOut, dependencies=[Depends(enforce_csrf)])
async def upsert_schedule(
    tenant_id: uuid.UUID,
    body: ReportScheduleIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> ReportScheduleOut:
    if body.frequency not in FREQUENCIES:
        raise HTTPException(status_code=400, detail="invalid frequency")
    if body.frequency == WEEKLY and body.weekday is None:
        raise HTTPException(status_code=400, detail="weekly schedule requires a weekday")
    if body.frequency != WEEKLY and body.weekday is not None:
        raise HTTPException(status_code=400, detail="weekday only valid for weekly")
    if body.device_id is not None:
        device = await session.get(Device, body.device_id)
        if device is None or device.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")
    try:
        recipients = normalize_recipients(body.recipients)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await ReportScheduleRepository(session, tenant_id).upsert(
        device_id=body.device_id, enabled=body.enabled, frequency=body.frequency,
        weekday=body.weekday, hour=body.hour, recipients=recipients, created_by=ctx.user.id,
        now=datetime.now(UTC), sections=body.sections,
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.upsert",
        target_type="report_schedule", target_id=str(row.id),
        ip=request.client.host if request.client else None,
        details={"device_id": str(body.device_id) if body.device_id else None,
                 "frequency": body.frequency, "enabled": body.enabled},
    )
    out = _out(row)
    await session.commit()
    return out


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(enforce_csrf)])
async def delete_schedule(
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await ReportScheduleRepository(session, tenant_id).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.delete",
        target_type="report_schedule", target_id=str(schedule_id), ip=None, details={},
    )
    await session.commit()


@router.post("/{schedule_id}/send-now", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(enforce_csrf)])
async def send_now(
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> Response:
    row = await ReportScheduleRepository(session, tenant_id).get(schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await enqueue("deliver_scheduled_report", str(schedule_id), True)
    return Response(status_code=status.HTTP_202_ACCEPTED)

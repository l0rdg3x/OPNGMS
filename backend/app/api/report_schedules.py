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
from app.repositories.report_settings import ReportSettingsRepository
from app.schemas.report_schedule import ReportScheduleIn, ReportScheduleOut
from app.services.audit import AuditService
from app.services.report_retention import limiting_store_for_sections
from app.services.report_schedule import (
    FREQUENCIES,
    MONTHLY,
    ON_DEMAND,
    WEEKLY,
    normalize_recipients,
)
from app.services.reporting.sections import resolve_sections

# Fixed-window days a scheduled run covers, for the retention check. Weekly = the prior 7 days.
# Monthly uses 30 (not the max month length of 31): the prior calendar month is 28-31 days, and the
# default metrics retention is exactly 30 — treating monthly as 31 would block a monthly schedule under
# the out-of-the-box defaults (surprising). 30 keeps the default config consistent; only a metrics
# retention LOWERED below 30 blocks a monthly schedule. A 31-day month with metrics kept at 30 renders
# its single oldest day as "no data" (the spec's accepted empty-period behavior — never a clamp).
_SCHEDULE_RANGE_DAYS = {WEEKLY: 7, MONTHLY: 30}

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
    # Report-side retention BLOCK (SP-1 PR4a): a fixed-window schedule (weekly/monthly) may not cover more
    # days than the tenant's effective retention for the stores its enabled sections read. on_demand has no
    # fixed range -> skip. Bound from resolve_sections(tenant default, this schedule's override).
    if body.frequency != ON_DEMAND:
        settings = await ReportSettingsRepository(session, tenant_id).get_or_default()
        enabled = resolve_sections(settings.sections, body.sections)
        limiting = await limiting_store_for_sections(session, tenant_id, enabled)
        range_days = _SCHEDULE_RANGE_DAYS[body.frequency]
        if limiting is not None and range_days > limiting[1]:
            store, days = limiting
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"{body.frequency} report needs {range_days} days but {store} "
                    f"is retained {days} days"
                ),
            )
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
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await ReportScheduleRepository(session, tenant_id).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.delete",
        target_type="report_schedule", target_id=str(schedule_id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()


@router.post("/{schedule_id}/send-now", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(enforce_csrf)])
async def send_now(
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> Response:
    row = await ReportScheduleRepository(session, tenant_id).get(schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await enqueue("deliver_scheduled_report", str(schedule_id), True)
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.send_now",
        target_type="report_schedule", target_id=str(schedule_id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()
    return Response(status_code=status.HTTP_202_ACCEPTED)

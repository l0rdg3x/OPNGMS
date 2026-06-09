import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.alert import Alert
from app.models.device import Device
from app.repositories.alert import AlertRepository
from app.repositories.metric import MAX_POINTS, MetricRepository
from app.schemas.alert import AlertOut
from app.schemas.health import HealthOut
from app.schemas.metric import MetricSeriesOut

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["monitoring"])


@router.get("/devices/{device_id}/metrics", response_model=MetricSeriesOut)
async def get_device_metrics(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    metric: str = Query(..., description="Nome metrica, es. 'cpu.load'"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    bucket_seconds: int | None = Query(None, alias="bucket", ge=1),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> MetricSeriesOut:
    now = datetime.now(timezone.utc)
    frm = from_ or (now - timedelta(hours=24))
    end = to or now
    bucket = timedelta(seconds=bucket_seconds) if bucket_seconds is not None else None
    if frm >= end:
        raise HTTPException(
            status_code=400,
            detail="Intervallo non valido: 'from' deve precedere 'to'",
        )
    if bucket is not None and (end - frm) / bucket > MAX_POINTS:
        raise HTTPException(
            status_code=400,
            detail=f"Troppi punti richiesti: intervallo/bucket supera {MAX_POINTS}",
        )
    repo = MetricRepository(session, tenant_id)
    points = await repo.series(device_id, metric, frm, end, bucket)
    last = await repo.last(device_id, metric)
    return MetricSeriesOut(metric=metric, points=points, last=last)


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    tenant_id: uuid.UUID,
    active: bool = Query(True, description="Solo alert attivi (resolved_at IS NULL)"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    alerts = await AlertRepository(session, tenant_id).list(active_only=active)
    return [AlertOut.model_validate(a) for a in alerts]


@router.get("/health", response_model=HealthOut)
async def fleet_health(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> HealthOut:
    status_rows = (
        await session.execute(
            select(Device.status, func.count())
            .where(Device.tenant_id == tenant_id)
            .group_by(Device.status)
        )
    ).all()
    by_status = {status: count for status, count in status_rows}
    total = sum(by_status.values())
    active_alerts = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.tenant_id == tenant_id, Alert.resolved_at.is_(None))
        )
    ).scalar_one()
    return HealthOut(total_devices=total, by_status=by_status, active_alerts=active_alerts)

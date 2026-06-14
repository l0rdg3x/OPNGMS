import uuid
from datetime import UTC, datetime, timedelta

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
from app.schemas.health import CountryCountOut, HealthOut
from app.schemas.metric import MetricSeriesOut
from app.schemas.perimeter import PerimeterAttackerOut
from app.services.geoip_provider import get_geoip
from app.services.reporting.aggregation import ReportAggregator

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["monitoring"])

_PERIMETER_KINDS = {"login_failed", "firewall_block"}

# Cap the breakdown so a widget/report never has to render an unbounded list of countries.
_ATTACKER_COUNTRIES_LIMIT = 20


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize a naive datetime to UTC (assumes UTC).

    The metric timestamps are `timestamptz`: for an internal console it is reasonable
    to assume UTC for values without a timezone, avoiding a TypeError in comparisons
    between naive and tz-aware datetimes (which would otherwise yield a 500).
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@router.get("/devices/{device_id}/metrics", response_model=MetricSeriesOut)
async def get_device_metrics(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    metric: str = Query(..., description="Metric name, e.g. 'cpu.load'"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    bucket_seconds: int | None = Query(None, alias="bucket", ge=1),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> MetricSeriesOut:
    now = datetime.now(UTC)
    # Normalize naive datetimes to UTC (e.g. ?from=2026-01-01T00:00:00 without Z)
    # before computing frm/end and the comparisons: avoids the naive-vs-aware TypeError.
    from_ = _ensure_utc(from_)
    to = _ensure_utc(to)
    frm = from_ or (now - timedelta(hours=24))
    end = to or now
    bucket = timedelta(seconds=bucket_seconds) if bucket_seconds is not None else None
    if frm >= end:
        raise HTTPException(
            status_code=400,
            detail="Invalid interval: 'from' must precede 'to'",
        )
    if bucket is not None and (end - frm) / bucket > MAX_POINTS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many points requested: interval/bucket exceeds {MAX_POINTS}",
        )
    repo = MetricRepository(session, tenant_id)
    points = await repo.series(device_id, metric, frm, end, bucket)
    last = await repo.last(device_id, metric)
    return MetricSeriesOut(metric=metric, points=points, last=last)


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    tenant_id: uuid.UUID,
    active: bool = Query(True, description="Active alerts only (resolved_at IS NULL)"),
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
    by_status = dict(status_rows)
    total = sum(by_status.values())
    active_alerts = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.tenant_id == tenant_id, Alert.resolved_at.is_(None))
        )
    ).scalar_one()
    return HealthOut(total_devices=total, by_status=by_status, active_alerts=active_alerts)


@router.get("/attacker-countries", response_model=list[CountryCountOut])
async def attacker_countries(
    tenant_id: uuid.UUID,
    frm: datetime | None = Query(None, alias="frm"),
    to: datetime | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[CountryCountOut]:
    """Top attacker countries (resolved from IDS `events.src_ip`) over the range, ranked by attempts.

    Defaults to the last 7 days. Resolution is offline via the cached DB-IP mmdb; if no mmdb is
    available the endpoint degrades to an empty list (never an error)."""
    now = datetime.now(UTC)
    frm = _ensure_utc(frm)
    to = _ensure_utc(to)
    start = frm or (now - timedelta(days=7))
    end = to or now
    if start >= end:
        raise HTTPException(status_code=400, detail="Invalid interval: 'frm' must precede 'to'")
    if end - start > timedelta(days=92):
        raise HTTPException(status_code=400, detail="Range must not exceed 92 days")
    geoip = await get_geoip(session)
    if geoip is None:
        return []
    rows = await ReportAggregator(session, tenant_id).attacker_countries(
        frm=start, to=end, device_id=device_id, limit=_ATTACKER_COUNTRIES_LIMIT, geoip=geoip,
    )
    return [CountryCountOut(code=r.code, count=r.count, pct=r.pct) for r in rows]


@router.get("/perimeter/attackers", response_model=list[PerimeterAttackerOut])
async def perimeter_attackers(
    tenant_id: uuid.UUID,
    kind: str = Query(...),
    frm: datetime | None = Query(None),
    to: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    device_id: uuid.UUID | None = Query(None),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[PerimeterAttackerOut]:
    """Top attacker IPs for a perimeter `kind` ('login_failed' | 'firewall_block'), ranked by cumulative
    count, active in the window (defaults to the last 7 days). Country is resolved offline via the
    cached mmdb (UNKNOWN if no mmdb). Serves both the Overview cards (small limit) and the /perimeter
    page (larger limit)."""
    if kind not in _PERIMETER_KINDS:
        raise HTTPException(status_code=422, detail=f"unknown kind: {kind!r}")
    now = datetime.now(UTC)
    start = _ensure_utc(frm) or (now - timedelta(days=7))
    end = _ensure_utc(to) or now
    if start >= end:
        raise HTTPException(status_code=400, detail="Invalid interval: 'frm' must precede 'to'")
    if end - start > timedelta(days=92):
        raise HTTPException(status_code=400, detail="Range must not exceed 92 days")
    geoip = await get_geoip(session)  # None is fine: country -> UNKNOWN (this view is IP-based)
    rows = await ReportAggregator(session, tenant_id).perimeter_top(
        kind=kind, frm=start, to=end, geoip=geoip, limit=limit, device_id=device_id,
    )
    return [
        PerimeterAttackerOut(src_ip=r.src_ip, country=r.country, count=r.count,
                             last_seen=r.last_seen, label=r.label)
        for r in rows
    ]

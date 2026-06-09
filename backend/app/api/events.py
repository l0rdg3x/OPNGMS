import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.repositories.event import MAX_EVENTS, EventRepository
from app.schemas.event import EventOut

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["events"])


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/events", response_model=list[EventOut])
async def list_events(
    tenant_id: uuid.UUID,
    source: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=MAX_EVENTS),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    repo = EventRepository(session, tenant_id)
    return await repo.list(
        source=source, device_id=device_id,
        frm=_ensure_utc(from_), to=_ensure_utc(to), limit=limit,
    )

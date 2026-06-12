import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.schemas.logs import LogHitOut, LogSearchIn, LogSearchOut
from app.services.log_search import MAX_SIZE, LogSearchError, search_logs

router = APIRouter(prefix="/api/tenants/{tenant_id}/logs", tags=["logs"])


@router.post("/search", response_model=LogSearchOut)
async def search_logs_endpoint(
    tenant_id: uuid.UUID,
    body: LogSearchIn,
    ctx: TenantContext = Depends(require_tenant(Action.LOG_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogSearchOut:
    s = get_settings()
    if body.to <= body.frm:
        raise HTTPException(status_code=400, detail="`to` must be after `frm`")
    if body.to - body.frm > timedelta(days=s.log_search_max_range_days):
        raise HTTPException(
            status_code=400,
            detail=f"range must not exceed {s.log_search_max_range_days} days",
        )
    # The operator-tunable `log_search_max_size` is the soft cap; MAX_SIZE is the
    # hard ceiling. This is the size actually sent to OpenSearch.
    effective_size = min(body.size, s.log_search_max_size, MAX_SIZE)
    if body.device_id is not None:
        device = await session.get(Device, body.device_id)
        if device is None or device.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")
    try:
        res = await search_logs(
            s,
            tenant_id=tenant_id,
            frm=body.frm,
            to=body.to,
            query=body.query,
            device_id=body.device_id,
            size=effective_size,
            cursor=body.cursor.model_dump() if body.cursor else None,
        )
    except LogSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="log search unavailable"
        ) from exc
    return LogSearchOut(
        total=res.total,
        next_cursor=res.next_cursor,
        hits=[
            LogHitOut(
                id=h.id,
                timestamp=h.timestamp,
                device_id=h.device_id,
                host=h.host,
                program=h.program,
                message=h.message,
                source=h.source,
            )
            for h in res.hits
        ],
    )

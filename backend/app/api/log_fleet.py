import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import require_org
from app.core.rbac import Action
from app.models.user import User
from app.repositories.tenant import TenantRepository
from app.schemas.log_fleet import LogFleetDevicesOut, LogFleetOut
from app.services.log_fleet import log_fleet_overview, tenant_device_fleet

router = APIRouter(prefix="/api/admin", tags=["log-fleet"])

# Selectable volume windows -> hours. Unknown values fall back to 24h.
_WINDOW_HOURS = {"24h": 24, "7d": 168, "30d": 720}


def _window(window: str) -> tuple[int, str]:
    return _WINDOW_HOURS.get(window, 24), (window if window in _WINDOW_HOURS else "24h")


@router.get("/log-fleet", response_model=LogFleetOut)
async def get_log_fleet(
    window: str = "24h",
    user: User = Depends(require_org(Action.LOG_FLEET_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogFleetOut:
    window_hours, label = _window(window)
    data = await log_fleet_overview(session, get_settings(), window_hours=window_hours)
    return LogFleetOut(**data, window=label)


@router.get("/log-fleet/tenants/{tenant_id}/devices", response_model=LogFleetDevicesOut)
async def get_log_fleet_tenant_devices(
    tenant_id: uuid.UUID,
    window: str = "24h",
    user: User = Depends(require_org(Action.LOG_FLEET_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogFleetDevicesOut:
    """Per-device drill-down for one tenant (superadmin cross-tenant view): every device + its
    forwarding status, last log, windowed volume and a per-device silent flag."""
    window_hours, label = _window(window)
    tenant = await TenantRepository(session).get(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    data = await tenant_device_fleet(session, get_settings(), tenant_id=tenant_id, window_hours=window_hours)
    return LogFleetDevicesOut(tenant_id=tenant_id, tenant_name=tenant.name, **data, window=label)

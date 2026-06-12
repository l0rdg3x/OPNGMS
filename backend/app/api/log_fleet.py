from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.log_fleet import LogFleetOut
from app.services.log_fleet import log_fleet_overview

router = APIRouter(prefix="/api/admin", tags=["log-fleet"])

# Selectable volume windows -> hours. Unknown values fall back to 24h.
_WINDOW_HOURS = {"24h": 24, "7d": 168, "30d": 720}


@router.get("/log-fleet", response_model=LogFleetOut)
async def get_log_fleet(
    window: str = "24h",
    user: User = Depends(require_org(Action.LOG_FLEET_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogFleetOut:
    window_hours = _WINDOW_HOURS.get(window, 24)
    label = window if window in _WINDOW_HOURS else "24h"
    data = await log_fleet_overview(session, get_settings(), window_hours=window_hours)
    return LogFleetOut(**data, window=label)

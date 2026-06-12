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


@router.get("/log-fleet", response_model=LogFleetOut)
async def get_log_fleet(
    user: User = Depends(require_org(Action.LOG_FLEET_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogFleetOut:
    data = await log_fleet_overview(session, get_settings())
    return LogFleetOut(**data)

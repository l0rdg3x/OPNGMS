import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange


class ConfigChangeRepository:
    """Tenant-scoped config change reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, device_id: uuid.UUID) -> Sequence[ConfigChange]:
        stmt = (
            select(ConfigChange)
            .where(
                ConfigChange.tenant_id == self.tenant_id,
                ConfigChange.device_id == device_id,
            )
            .order_by(ConfigChange.created_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, change_id: uuid.UUID) -> ConfigChange | None:
        stmt = select(ConfigChange).where(
            ConfigChange.id == change_id,
            ConfigChange.tenant_id == self.tenant_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_snapshot import ConfigSnapshot


class ConfigSnapshotRepository:
    """Tenant-scoped config snapshot reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, device_id: uuid.UUID) -> Sequence[ConfigSnapshot]:
        stmt = (
            select(ConfigSnapshot)
            .where(
                ConfigSnapshot.tenant_id == self.tenant_id,
                ConfigSnapshot.device_id == device_id,
            )
            .order_by(ConfigSnapshot.taken_at.desc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def get(self, snapshot_id: uuid.UUID) -> ConfigSnapshot | None:
        stmt = select(ConfigSnapshot).where(
            ConfigSnapshot.id == snapshot_id,
            ConfigSnapshot.tenant_id == self.tenant_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

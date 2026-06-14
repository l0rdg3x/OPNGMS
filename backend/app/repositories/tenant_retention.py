import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_retention import TenantRetention


class TenantRetentionRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def _get(self) -> TenantRetention | None:
        return (await self.session.execute(
            select(TenantRetention).where(TenantRetention.tenant_id == self.tenant_id)
        )).scalar_one_or_none()

    async def get_overrides(self) -> dict:
        row = await self._get()
        return dict(row.overrides) if row else {}

    async def upsert(self, patch: dict) -> dict:
        """Merge `patch` into the stored overrides; a key set to None is removed (back to inherit)."""
        row = await self._get()
        if row is None:
            row = TenantRetention(tenant_id=self.tenant_id, overrides={})
            self.session.add(row)
        merged = {**row.overrides}
        for k, v in patch.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        row.overrides = merged
        await self.session.flush()
        return merged

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_settings import ReportSettings


class ReportSettingsRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def get(self) -> ReportSettings | None:
        return (
            await self.session.execute(
                select(ReportSettings).where(ReportSettings.tenant_id == self.tenant_id)
            )
        ).scalar_one_or_none()

    async def get_or_default(self) -> ReportSettings:
        row = await self.get()
        if row is not None:
            return row
        # Return a transient (not persisted) object with sensible defaults.
        return ReportSettings(
            tenant_id=self.tenant_id,
            title="Security & Activity Report",
            owner="",
            timezone="UTC",
        )

    async def upsert(self, *, title: str, owner: str, timezone: str) -> ReportSettings:
        row = await self.get()
        if row is None:
            row = ReportSettings(tenant_id=self.tenant_id)
            self.session.add(row)
        row.title, row.owner, row.timezone = title, owner, timezone
        await self.session.flush()
        return row

    async def set_logo(self, logo: bytes, mime: str) -> None:
        row = await self.get()
        if row is None:
            row = ReportSettings(tenant_id=self.tenant_id)
            self.session.add(row)
        row.logo, row.logo_mime = logo, mime
        await self.session.flush()

    async def clear_logo(self) -> None:
        row = await self.get()
        if row is not None:
            row.logo, row.logo_mime = None, None
            await self.session.flush()

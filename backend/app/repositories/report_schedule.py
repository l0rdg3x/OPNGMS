import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_schedule import ReportSchedule
from app.services.report_schedule import next_run_at


class ReportScheduleRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self) -> list[ReportSchedule]:
        return list((await self.session.execute(
            select(ReportSchedule).where(ReportSchedule.tenant_id == self.tenant_id)
            .order_by(ReportSchedule.device_id.nullsfirst())
        )).scalars().all())

    async def get(self, schedule_id: uuid.UUID) -> ReportSchedule | None:
        return (await self.session.execute(
            select(ReportSchedule).where(
                ReportSchedule.tenant_id == self.tenant_id, ReportSchedule.id == schedule_id
            )
        )).scalar_one_or_none()

    async def _get_by_scope(self, device_id: uuid.UUID | None) -> ReportSchedule | None:
        stmt = select(ReportSchedule).where(ReportSchedule.tenant_id == self.tenant_id)
        stmt = stmt.where(ReportSchedule.device_id.is_(None) if device_id is None
                          else ReportSchedule.device_id == device_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, *, device_id: uuid.UUID | None, enabled: bool, frequency: str,
                     weekday: int | None, hour: int, recipients: list[str],
                     created_by: uuid.UUID | None, now: datetime,
                     sections: dict[str, bool] | None = None) -> ReportSchedule:
        row = await self._get_by_scope(device_id)
        if row is None:
            row = ReportSchedule(tenant_id=self.tenant_id, device_id=device_id, created_by=created_by)
            self.session.add(row)
        row.enabled, row.frequency, row.weekday, row.hour = enabled, frequency, weekday, hour
        row.recipients = recipients
        row.sections = sections
        row.next_run_at = next_run_at(frequency, weekday, hour, after=now) if enabled else None
        await self.session.flush()
        return row

    async def delete(self, schedule_id: uuid.UUID) -> bool:
        row = await self.get(schedule_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True

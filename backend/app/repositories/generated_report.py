import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generated_report import GeneratedReport

# Metadata columns (everything except the pdf bytes) for the list view.
_META = (
    GeneratedReport.id, GeneratedReport.kind, GeneratedReport.period_from,
    GeneratedReport.period_to, GeneratedReport.created_by, GeneratedReport.size,
    GeneratedReport.created_at,
)


class GeneratedReportRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def create(self, *, kind: str, period_from: datetime, period_to: datetime,
                     created_by: uuid.UUID | None, pdf: bytes) -> GeneratedReport:
        row = GeneratedReport(
            tenant_id=self.tenant_id, kind=kind, period_from=period_from, period_to=period_to,
            created_by=created_by, pdf=pdf, size=len(pdf),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list(self) -> list:
        # Metadata only (no pdf bytes), newest first.
        rows = (
            await self.session.execute(
                select(*_META).where(GeneratedReport.tenant_id == self.tenant_id)
                .order_by(GeneratedReport.created_at.desc())
            )
        ).all()
        return rows  # Row objects with .id/.kind/.period_from/.period_to/.created_by/.size/.created_at

    async def get(self, report_id: uuid.UUID) -> GeneratedReport | None:
        return (
            await self.session.execute(
                select(GeneratedReport).where(
                    GeneratedReport.tenant_id == self.tenant_id, GeneratedReport.id == report_id
                )
            )
        ).scalar_one_or_none()

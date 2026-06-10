import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.generated_report import GeneratedReport


async def test_generated_report_insert_and_read(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pdf_bytes = b"%PDF-1.4 fake pdf content"
    period_from = datetime(2026, 5, 1, tzinfo=timezone.utc)
    period_to = datetime(2026, 6, 1, tzinfo=timezone.utc)
    created_by = uuid.uuid4()

    row_id: uuid.UUID
    async with factory() as s:  # owner session -> bypasses RLS
        row = GeneratedReport(
            tenant_id=tenant_a,
            kind="on_demand",
            period_from=period_from,
            period_to=period_to,
            created_by=created_by,
            pdf=pdf_bytes,
            size=len(pdf_bytes),
        )
        s.add(row)
        await s.flush()
        row_id = row.id
        await s.commit()

    async with factory() as s:
        fetched = await s.get(GeneratedReport, row_id)

    assert fetched is not None
    assert fetched.kind == "on_demand"
    assert fetched.size == len(pdf_bytes)
    assert fetched.pdf == pdf_bytes
    assert fetched.created_by == created_by
    assert fetched.tenant_id == tenant_a
    assert fetched.created_at is not None


async def test_generated_report_size_matches_pdf(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    pdf_bytes = b"%PDF-1.4 " + b"x" * 512
    period_from = datetime(2026, 4, 1, tzinfo=timezone.utc)
    period_to = datetime(2026, 5, 1, tzinfo=timezone.utc)

    async with factory() as s:
        row = GeneratedReport(
            tenant_id=tenant_a,
            kind="scheduled",
            period_from=period_from,
            period_to=period_to,
            created_by=None,
            pdf=pdf_bytes,
            size=len(pdf_bytes),
        )
        s.add(row)
        await s.flush()
        row_id = row.id
        await s.commit()

    async with factory() as s:
        fetched = await s.get(GeneratedReport, row_id)

    assert fetched is not None
    assert fetched.size == len(pdf_bytes)
    assert fetched.kind == "scheduled"
    assert fetched.created_by is None

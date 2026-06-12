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


async def test_generated_report_stores_device_id(db_engine):
    import uuid
    from datetime import datetime, timezone
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.repositories.generated_report import GeneratedReportRepository

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'A','a','active')"), {"id": tid})
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        row = await GeneratedReportRepository(s, tid).create(
            kind="scheduled", period_from=datetime(2026,6,1,tzinfo=timezone.utc),
            period_to=datetime(2026,6,8,tzinfo=timezone.utc),
            created_by=None, pdf=b"%PDF-", device_id=did)
        assert row.device_id == did

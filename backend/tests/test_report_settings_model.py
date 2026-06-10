import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.report_settings import ReportSettings


async def test_report_settings_insert_defaults(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner session -> bypasses RLS
        row = ReportSettings(tenant_id=tenant_a)
        s.add(row)
        await s.commit()
    async with factory() as s:
        fetched = await s.get(ReportSettings, tenant_a)
    assert fetched is not None
    assert fetched.title == "Security & Activity Report"
    assert fetched.timezone == "UTC"
    assert fetched.owner == ""
    assert fetched.logo is None
    assert fetched.logo_mime is None
    assert fetched.updated_at is not None


async def test_report_settings_explicit_values(db_engine, two_tenants):
    _, tenant_b = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid = uuid.uuid4()
    async with factory() as s:
        # Insert a second tenant to avoid FK issues with tenant_b already used
        from sqlalchemy import text
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'C', 'c', 'active')"),
            {"id": tid},
        )
        row = ReportSettings(
            tenant_id=tid,
            title="Custom Report",
            owner="ACME Corp",
            timezone="Europe/Rome",
        )
        s.add(row)
        await s.commit()
    async with factory() as s:
        fetched = await s.get(ReportSettings, tid)
    assert fetched.title == "Custom Report"
    assert fetched.owner == "ACME Corp"
    assert fetched.timezone == "Europe/Rome"
    assert fetched.logo is None

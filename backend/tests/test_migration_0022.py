"""0022 creates smtp_settings + report_schedule (+RLS) and adds report_settings.from_email
and generated_reports.device_id. (db_engine builds head schema from metadata; assert objects exist.)"""
from sqlalchemy import text


async def test_migration_0022_schema(db_engine):
    async with db_engine.begin() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='report_settings'"
        ))).scalars().all()
        assert "from_email" in cols
        gcols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='generated_reports'"
        ))).scalars().all()
        assert "device_id" in gcols
        tabs = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('smtp_settings','report_schedule')"
        ))).scalars().all()
        assert set(tabs) == {"smtp_settings", "report_schedule"}
        rls = (await conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='report_schedule'"
        ))).one()
        assert rls == (True, True)

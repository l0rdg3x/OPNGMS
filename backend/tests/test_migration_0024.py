from sqlalchemy import text


async def test_migration_0024(db_engine):
    async with db_engine.begin() as conn:
        tabs = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('syslog_ca','device_log_forwarding')"
        ))).scalars().all()
        assert set(tabs) == {"syslog_ca", "device_log_forwarding"}
        rls = (await conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='device_log_forwarding'"
        ))).one()
        assert rls == (True, True)

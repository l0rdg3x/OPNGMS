from sqlalchemy import text


async def test_migration_0023_columns(db_engine):
    async with db_engine.begin() as conn:
        cc = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='config_changes'"
        ))).scalars().all()
        assert "sweep_attempts" in cc
        assert "reverts_change_id" in cc
        fa = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='firmware_actions'"
        ))).scalars().all()
        assert "sweep_attempts" in fa

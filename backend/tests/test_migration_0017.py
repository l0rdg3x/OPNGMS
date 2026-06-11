from sqlalchemy import text


async def test_pre_apply_snapshot_column_exists(db_engine):
    async with db_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='config_changes'"
        ))).scalars().all()
    assert "pre_apply_snapshot_id" in cols

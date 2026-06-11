from sqlalchemy import text


async def test_device_edition_columns_exist(db_engine):
    async with db_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='devices'"
        ))).scalars().all()
    assert "edition" in cols
    assert "firmware_series" in cols

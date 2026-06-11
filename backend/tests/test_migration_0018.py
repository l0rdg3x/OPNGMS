from sqlalchemy import text


async def test_firmware_actions_table_exists(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name='firmware_actions'"
        ))).scalars().all()
    assert "firmware_actions" in tables

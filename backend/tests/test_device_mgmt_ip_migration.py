from sqlalchemy import text


async def test_devices_has_mgmt_source_ip_column(db_engine):
    async with db_engine.begin() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='devices' AND column_name='mgmt_source_ip'"))).scalars().all()
    assert cols == ["mgmt_source_ip"]

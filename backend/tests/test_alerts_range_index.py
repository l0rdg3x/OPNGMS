from sqlalchemy import text


async def test_alerts_range_index_exists(db_engine):
    """The composite index backing alerts_in_range exists with the expected (tenant_id, device_id,
    opened_at) columns, in order. Declared on the Alert model + migration 0041."""
    async with db_engine.begin() as conn:
        cols = (await conn.execute(text(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE c.relname = 'ix_alerts_tenant_device_opened' "
            "ORDER BY array_position(i.indkey, a.attnum)"
        ))).scalars().all()
    assert cols == ["tenant_id", "device_id", "opened_at"]

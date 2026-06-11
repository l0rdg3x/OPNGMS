from sqlalchemy import text

from app.core.rls import TENANT_TABLES


def test_profiles_are_global_not_tenant_tables():
    assert "config_profiles" not in TENANT_TABLES
    assert "config_profile_members" not in TENANT_TABLES


async def test_profile_tables_and_tag_exist(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('config_profiles','config_profile_members')"
        ))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='config_changes' AND column_name='source_profile_id'"
        ))).scalars().all()
    assert set(tables) == {"config_profiles", "config_profile_members"}
    assert cols == ["source_profile_id"]

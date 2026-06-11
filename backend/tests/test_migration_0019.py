from sqlalchemy import text

from app.core.rls import TENANT_TABLES


def test_overrides_in_tenant_tables_but_library_is_global():
    assert "template_overrides" in TENANT_TABLES        # RLS-managed
    assert "config_templates" not in TENANT_TABLES       # global, no tenant policy


async def test_tables_and_tag_column_exist(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('config_templates','template_overrides')"
        ))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='config_changes' AND column_name='source_template_id'"
        ))).scalars().all()
    assert "config_templates" in tables and "template_overrides" in tables
    assert cols == ["source_template_id"]

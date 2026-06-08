from app.core.rls import (
    TENANT_TABLES,
    disable_rls_statements,
    enable_rls_statements,
)


def test_rls_statements_cover_devices():
    assert "devices" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.current_tenant'" in sql
    assert "WITH CHECK" in sql
    assert "tenant_id" in sql


def test_disable_rls_statements_tear_down_policy():
    sql = "\n".join(disable_rls_statements())
    assert "DROP POLICY IF EXISTS" in sql
    assert "DISABLE ROW LEVEL SECURITY" in sql

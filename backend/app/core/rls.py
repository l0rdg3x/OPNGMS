"""RLS statements for the tenant-data tables.

Single source used both by the migrations and by the tests' conftest, so the
policies applied in production and in tests cannot diverge.
"""

TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events", "config_snapshots", "config_changes", "report_settings", "generated_reports", "firmware_actions", "template_overrides", "report_schedule", "device_log_forwarding", "revoked_syslog_certs", "perimeter_attacker"]

POLICY_NAME = "tenant_isolation"


def _policy_predicate() -> str:
    # NULLIF: missing context or '' -> NULL -> no rows (fail-closed).
    return "tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def policy_create_statement(table: str) -> str:
    predicate = _policy_predicate()
    return (
        f"CREATE POLICY {POLICY_NAME} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def enable_rls_statements(tables: list[str] | None = None) -> list[str]:
    # `tables` lets historical migrations pin the subset of tables that existed
    # at their point in history (0002 covers only `devices`; metrics/alerts come
    # later, enabled by 0007). Without an argument it uses all the current
    # TENANT_TABLES (tests' conftest).
    target = TENANT_TABLES if tables is None else tables
    stmts: list[str] = []
    for table in target:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: RLS also applies to the table owner.
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        stmts.append(policy_create_statement(table))
    return stmts


def recreate_policy_statements(tables: list[str] | None = None) -> list[str]:
    """DROP + CREATE to update the policy on already-migrated DBs (migration 0003)."""
    target = TENANT_TABLES if tables is None else tables
    stmts: list[str] = []
    for table in target:
        stmts.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        stmts.append(policy_create_statement(table))
    return stmts


def disable_rls_statements(tables: list[str] | None = None) -> list[str]:
    target = TENANT_TABLES if tables is None else tables
    stmts: list[str] = []
    for table in target:
        stmts.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return stmts

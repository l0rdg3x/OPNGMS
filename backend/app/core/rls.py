"""Statement RLS per le tabelle di dati-tenant.

Fonte unica usata sia dalle migrazioni sia dalla conftest dei test, cosi' le
policy applicate in produzione e in test non possono divergere.
"""

TENANT_TABLES: list[str] = ["devices"]

POLICY_NAME = "tenant_isolation"


def _policy_predicate() -> str:
    # NULLIF: contesto assente o '' -> NULL -> nessuna riga (fail-closed).
    return "tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def policy_create_statement(table: str) -> str:
    predicate = _policy_predicate()
    return (
        f"CREATE POLICY {POLICY_NAME} ON {table} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def enable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: la RLS si applica anche al proprietario della tabella.
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        stmts.append(policy_create_statement(table))
    return stmts


def recreate_policy_statements() -> list[str]:
    """DROP + CREATE per aggiornare la policy su DB gia' migrati (migrazione 0003)."""
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        stmts.append(policy_create_statement(table))
    return stmts


def disable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return stmts

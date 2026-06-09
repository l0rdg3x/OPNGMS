"""Statement RLS per le tabelle di dati-tenant.

Fonte unica usata sia dalle migrazioni sia dalla conftest dei test, cosi' le
policy applicate in produzione e in test non possono divergere.
"""

TENANT_TABLES: list[str] = ["devices", "metrics", "alerts"]

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


def enable_rls_statements(tables: list[str] | None = None) -> list[str]:
    # `tables` permette alle migrazioni storiche di fissare il sottoinsieme di
    # tabelle esistente al loro punto della cronologia (la 0002 copre solo
    # `devices`; metrics/alerts arrivano dopo, abilitate dalla 0007). Senza
    # argomento usa tutte le TENANT_TABLES correnti (conftest dei test).
    target = TENANT_TABLES if tables is None else tables
    stmts: list[str] = []
    for table in target:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: la RLS si applica anche al proprietario della tabella.
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        stmts.append(policy_create_statement(table))
    return stmts


def recreate_policy_statements(tables: list[str] | None = None) -> list[str]:
    """DROP + CREATE per aggiornare la policy su DB gia' migrati (migrazione 0003)."""
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

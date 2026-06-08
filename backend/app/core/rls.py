"""Statement RLS per le tabelle di dati-tenant.

Fonte unica usata sia dalla migrazione 0002 sia dalla conftest dei test, così
policy applicate in produzione e in test non possono divergere.
"""

# Tabelle soggette a isolamento per tenant (le tabelle di control-plane NON sono qui).
TENANT_TABLES: list[str] = ["devices"]


def enable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: la RLS si applica anche al proprietario della tabella (e quindi nei test).
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        stmts.append(
            f"DO $$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='{table}' AND policyname='tenant_isolation') THEN "
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid) "
            f"WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid); "
            f"END IF; END $$"
        )
    return stmts


def disable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return stmts

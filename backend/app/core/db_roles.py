"""Ruolo applicativo non-superuser per far valere la RLS a runtime.

I superuser PostgreSQL bypassano sempre la RLS (anche con FORCE). L'app deve
quindi connettersi con un ruolo NON-superuser e NOBYPASSRLS; migrazioni e setup
girano come owner/superuser. Fonte unica usata sia dalla migrazione 0003 sia
dalla conftest dei test.
"""

APP_ROLE = "opngms_app"
# MVP/locale: in produzione cambiare con `ALTER ROLE opngms_app PASSWORD '...'`
# E AGGIORNARE di conseguenza DATABASE_URL (l'app si connette con queste credenziali).
APP_ROLE_PASSWORD = "opngms_app"


def create_app_role_statements() -> list[str]:
    # CREATE-or-ALTER: garantisce gli attributi anche se il ruolo esiste gia'
    # (es. creato da una run precedente della conftest).
    return [
        f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        ELSE
            ALTER ROLE {APP_ROLE} LOGIN PASSWORD '{APP_ROLE_PASSWORD}'
                NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
        END IF; END $$"""
    ]


def grant_app_role_statements() -> list[str]:
    return [
        f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    ]


def drop_app_role_statements() -> list[str]:
    # Il ruolo e' a livello di cluster ma i privilegi sono per-database: revochiamo
    # e facciamo DROP OWNED solo nel DB corrente. DROP ROLE e' cluster-wide e
    # fallisce finche' un altro DB del cluster (es. dev vs test, che condividono il
    # cluster) concede ancora privilegi al ruolo. Per questo droppiamo il ruolo solo
    # quando non restano dipendenze in nessun database (pg_shdepend vuoto); altrimenti
    # lo lasciamo perche' serve ancora altrove. Downgrade idempotente e fail-safe.
    return [
        f"""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{APP_ROLE}') THEN
            EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM {APP_ROLE}';
            EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {APP_ROLE}';
            EXECUTE 'REVOKE ALL ON SCHEMA public FROM {APP_ROLE}';
            EXECUTE 'DROP OWNED BY {APP_ROLE}';
            IF NOT EXISTS (
                SELECT 1 FROM pg_shdepend s
                WHERE s.refobjid = (SELECT oid FROM pg_roles WHERE rolname='{APP_ROLE}')
            ) THEN
                EXECUTE 'DROP ROLE {APP_ROLE}';
            END IF;
        END IF; END $$"""
    ]

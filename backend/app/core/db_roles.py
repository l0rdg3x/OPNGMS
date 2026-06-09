"""Non-superuser application role to enforce RLS at runtime.

PostgreSQL superusers always bypass RLS (even with FORCE). The app must therefore
connect with a NON-superuser, NOBYPASSRLS role; migrations and setup run as
owner/superuser. Single source used both by migration 0003 and by the tests'
conftest.
"""

APP_ROLE = "opngms_app"
# MVP/local: in production change it with `ALTER ROLE opngms_app PASSWORD '...'`
# AND UPDATE DATABASE_URL accordingly (the app connects with these credentials).
APP_ROLE_PASSWORD = "opngms_app"


def create_app_role_statements() -> list[str]:
    # CREATE-or-ALTER: ensures the attributes even if the role already exists
    # (e.g. created by a previous conftest run).
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
    # The role is cluster-wide but the privileges are per-database: we revoke
    # and DROP OWNED only in the current DB. DROP ROLE is cluster-wide and
    # fails as long as another DB in the cluster (e.g. dev vs test, which share the
    # cluster) still grants privileges to the role. That is why we drop the role only
    # when no dependencies remain in any database (pg_shdepend empty); otherwise
    # we leave it because it is still needed elsewhere. Idempotent and fail-safe downgrade.
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

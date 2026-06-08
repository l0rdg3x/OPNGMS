import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.rls import enable_rls_statements
from app.main import app
from app.models import Base

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


APP_ROLE = "opngms_app"


@pytest.fixture
async def db_engine():
    """Create a fresh test DB schema with RLS enabled for each test function.

    Function-scoped (not session-scoped) to avoid pytest-asyncio loop-scope
    conflicts between a session-scoped async fixture and function-scoped tests.
    Rebuilding the schema for 3 tests is acceptable overhead.

    The opngms user is a superuser, which PostgreSQL exempts from RLS even with
    FORCE ROW LEVEL SECURITY.  We therefore create a non-superuser role
    (opngms_app) and grant it the necessary privileges.  Tests that need genuine
    RLS enforcement issue SET ROLE opngms_app before querying.
    """
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL non impostata")
    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in enable_rls_statements():
            await conn.execute(text(stmt))
        # Create a non-superuser app role that RLS applies to
        await conn.execute(
            text(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{APP_ROLE}') THEN CREATE ROLE {APP_ROLE} LOGIN PASSWORD 'opngms_app'; END IF; END $$")
        )
        await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}"))
    yield engine
    await engine.dispose()


@pytest.fixture
async def two_tenants(db_engine):
    """Insert two tenants + one device each, returning (tenant_a_id, tenant_b_id)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    a, b = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status) "
                "VALUES (:id, 'A', 'a', 'active')"
            ),
            {"id": a},
        )
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status) "
                "VALUES (:id, 'B', 'b', 'active')"
            ),
            {"id": b},
        )
        # Insert device for tenant A under tenant A's RLS context
        await set_tenant_context(s, a)
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw-a', 'https://a', ''::bytea, ''::bytea, true, 'unverified', '{}')"
            ),
            {"id": uuid.uuid4(), "t": a},
        )
        # Insert device for tenant B under tenant B's RLS context
        await set_tenant_context(s, b)
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw-b', 'https://b', ''::bytea, ''::bytea, true, 'unverified', '{}')"
            ),
            {"id": uuid.uuid4(), "t": b},
        )
        await s.commit()
    return a, b

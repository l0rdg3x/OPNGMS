import os
import uuid

from cryptography.fernet import Fernet

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://opngms:opngms@localhost:5432/opngms"
)
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
os.environ.setdefault("MASTER_KEY", Fernet.generate_key().decode())
os.environ.setdefault("ADMIN_DATABASE_URL", "postgresql+asyncpg://opngms:opngms@localhost:5432/opngms")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import get_session, make_engine, set_tenant_context
from app.core.db_roles import (
    create_app_role_statements,
    grant_app_role_statements,
)
from app.core.rls import enable_rls_statements
from app.main import app
from app.models import Base

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


def csrf_headers(client) -> dict:
    """Return an X-OPNGMS-CSRF header dict with the token from the client's cookie jar.

    Call this after a successful login so the real per-session CSRF token is used.
    """
    token = client.cookies.get("opngms_csrf") or ""
    return {"X-OPNGMS-CSRF": token}


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_engine():
    """Create a fresh test DB schema with RLS enabled for each test function.

    Function-scoped (not session-scoped) to avoid pytest-asyncio loop-scope
    conflicts between a session-scoped async fixture and function-scoped tests.
    Rebuilding the schema for 3 tests is acceptable overhead.

    The opngms user is a superuser, which PostgreSQL exempts from RLS even with
    FORCE ROW LEVEL SECURITY.  We therefore create the non-superuser role
    opngms_app and grant it the necessary privileges, reusing the same statements
    as migration 0003 (db_roles) so test and production cannot diverge.  Tests
    that need genuine RLS enforcement either SET ROLE opngms_app or connect as the
    real opngms_app login role before querying.
    """
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL non impostata")
    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("SELECT create_hypertable('metrics', 'time', if_not_exists => true)"))
        await conn.execute(text("SELECT create_hypertable('events', 'time', if_not_exists => true)"))
        for stmt in enable_rls_statements():
            await conn.execute(text(stmt))
        # Create the non-superuser app role and grant it data-table privileges,
        # using the same statements as migration 0003 (DRY: test and prod cannot
        # diverge). RLS applies to this role exactly as in production.
        for stmt in create_app_role_statements():
            await conn.execute(text(stmt))
        for stmt in grant_app_role_statements():
            await conn.execute(text(stmt))
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


@pytest.fixture
async def api_client(db_engine):
    """ASGI client with get_session overridden toward the test DB (owner role)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    # base_url https:// so httpx stores the `secure=True` cookies (ASGITransport does no real TLS).
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def app_role_api_client(db_engine):
    """Like api_client, but the session connects as opngms_app (non-superuser) -> RLS active."""
    app_url = make_url(TEST_DB_URL).set(username="opngms_app", password="opngms_app")
    engine = make_engine(app_url.render_as_string(hide_password=False))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()

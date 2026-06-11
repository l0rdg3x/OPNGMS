import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.core.rls import TENANT_TABLES
from tests.factories import make_tenant


def test_firmware_actions_in_tenant_tables():
    """Static guard: the table must be RLS-managed (in TENANT_TABLES)."""
    assert "firmware_actions" in TENANT_TABLES


async def _seed_device(s, tenant_id, name):
    """As owner (RLS-bypassing): insert a device. Returns device_id."""
    did = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO devices "
            "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
        ),
        {"id": did, "t": tenant_id, "n": name},
    )
    return did


async def _seed_firmware_action(s, tenant_id, device_id, kind="firmware_update"):
    """As owner (RLS-bypassing): insert a firmware_action row. Returns action_id."""
    aid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO firmware_actions "
            "(id, tenant_id, device_id, created_by, kind) "
            "VALUES (:id, :t, :d, :u, :k)"
        ),
        {"id": aid, "t": tenant_id, "d": device_id, "u": uuid.uuid4(), "k": kind},
    )
    return aid


async def test_firmware_actions_isolated_by_rls(db_engine):
    """Proof that RLS (not the application filter) isolates firmware_actions cross-tenant.

    Two tenants, one firmware_action each. Using the real opngms_app role with
    only tenant A's context set, a raw SELECT *without* WHERE tenant_id must return
    ONLY tenant A's row. If RLS were off this assertion would return both rows.
    Also verifies that with no tenant context set at all, RLS hides everything (fail-closed).
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Seed tenants + devices + firmware_actions as owner (bypasses RLS).
    async with factory() as s:
        a = await make_tenant(s, slug="fw-rls-a")
        b = await make_tenant(s, slug="fw-rls-b")
        await s.flush()
        ta, tb = a.id, b.id

        dev_a = await _seed_device(s, ta, "fw-dev-a")
        dev_b = await _seed_device(s, tb, "fw-dev-b")

        action_a = await _seed_firmware_action(s, ta, dev_a)
        action_b = await _seed_firmware_action(s, tb, dev_b)
        await s.commit()

    # Connect as opngms_app (non-superuser) so RLS is enforced.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        raw_factory = async_sessionmaker(engine, expire_on_commit=False)

        # With tenant A's context: raw query (no WHERE tenant_id) sees ONLY A's row.
        async with raw_factory() as s:
            await set_tenant_context(s, ta)
            ids = (
                await s.execute(
                    text("SELECT id FROM firmware_actions ORDER BY created_at")
                )
            ).scalars().all()
            assert ids == [action_a], (
                f"Expected only action_a ({action_a}), got: {ids}. "
                "RLS may not be enabled on firmware_actions."
            )

        # With no tenant context at all -> RLS hides everything (fail-closed).
        async with raw_factory() as s2:
            none = (
                await s2.execute(text("SELECT id FROM firmware_actions"))
            ).scalars().all()
            assert none == [], (
                f"Expected no rows without tenant context, got: {none}. "
                "RLS fail-closed check failed."
            )
    finally:
        await engine.dispose()

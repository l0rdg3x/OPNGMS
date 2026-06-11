import json
import os
import uuid

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.core.rls import TENANT_TABLES
from tests.factories import make_tenant

_VALID_BODY = {"name": "web", "type": "host", "content": ["1.2.3.4"]}


def test_template_tables_rls_membership():
    """Static guard: the per-tenant override table is RLS-managed; the global library is NOT."""
    assert "template_overrides" in TENANT_TABLES
    assert "config_templates" not in TENANT_TABLES


async def _seed_template(s, *, name="rls-tpl"):
    """As owner (RLS-bypassing): insert a global config_templates row. Returns template_id."""
    tpl_id = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO config_templates "
            "(id, kind, name, description, body, version, created_by) "
            "VALUES (:id, 'firewall_alias', :n, '', CAST(:b AS jsonb), 1, :u)"
        ),
        {"id": tpl_id, "n": name, "b": json.dumps(_VALID_BODY), "u": uuid.uuid4()},
    )
    return tpl_id


async def _seed_override(s, tenant_id, template_id, body_patch=None):
    """As owner (RLS-bypassing): insert a template_overrides row. Returns override_id."""
    oid = uuid.uuid4()
    await s.execute(
        text(
            "INSERT INTO template_overrides "
            "(id, template_id, tenant_id, body_patch) "
            "VALUES (:id, :tpl, :t, CAST(:p AS jsonb))"
        ),
        {"id": oid, "tpl": template_id, "t": tenant_id, "p": json.dumps(body_patch or {})},
    )
    return oid


async def test_template_overrides_isolated_by_rls(db_engine):
    """Proof that RLS (not the application filter) isolates template_overrides cross-tenant.

    Two tenants share one global template, with one override each. Using the real
    opngms_app role with only tenant A's context set, a raw SELECT *without* WHERE
    tenant_id must return ONLY tenant A's override. With no tenant context at all,
    RLS must hide everything (fail-closed). config_templates, being global, is not
    RLS-scoped, so the shared template row remains visible regardless of context.
    """
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Seed tenants + a shared global template + an override per tenant as owner (bypasses RLS).
    async with factory() as s:
        a = await make_tenant(s, slug="tpl-rls-a")
        b = await make_tenant(s, slug="tpl-rls-b")
        await s.flush()
        ta, tb = a.id, b.id

        template_id = await _seed_template(s)
        override_a = await _seed_override(s, ta, template_id, {"content": ["10.0.0.1"]})
        override_b = await _seed_override(s, tb, template_id, {"content": ["10.0.0.2"]})
        await s.commit()

    # Connect as opngms_app (non-superuser) so RLS is enforced.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username=APP_ROLE, password=APP_ROLE_PASSWORD
    )
    assert app_url.username == APP_ROLE  # fail loudly if the role was not applied
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        raw_factory = async_sessionmaker(engine, expire_on_commit=False)

        # With tenant A's context: raw query (no WHERE tenant_id) sees ONLY A's override.
        async with raw_factory() as s:
            await set_tenant_context(s, ta)
            ids = (
                await s.execute(
                    text("SELECT id FROM template_overrides ORDER BY created_at")
                )
            ).scalars().all()
            assert ids == [override_a], (
                f"Expected only override_a ({override_a}), got: {ids}. "
                "RLS may not be enabled on template_overrides."
            )
            assert override_b not in ids

            # The global template stays visible (config_templates is NOT tenant-scoped).
            tpl_ids = (
                await s.execute(text("SELECT id FROM config_templates"))
            ).scalars().all()
            assert template_id in tpl_ids

        # With no tenant context at all -> RLS hides every override (fail-closed).
        async with raw_factory() as s2:
            none = (
                await s2.execute(text("SELECT id FROM template_overrides"))
            ).scalars().all()
            assert none == [], (
                f"Expected no rows without tenant context, got: {none}. "
                "RLS fail-closed check failed."
            )
    finally:
        await engine.dispose()

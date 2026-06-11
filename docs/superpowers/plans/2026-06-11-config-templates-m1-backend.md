# Configuration Templates — M1 Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend for the configuration-template engine, M1: a global MSP **template library** (`firewall_alias` kind) managed by the superadmin, a **per-tenant override** layer, and an **apply** path that materializes a `config_change` and reuses the verified config-push pipeline (preview → now/scheduled push → snapshot rollback).

**Architecture:** Two new tables (`config_templates` global/no-RLS, `template_overrides` tenant-RLS) + a `config_changes.source_template_id` tag; a `templates` service that validates the typed `firewall_alias` body, computes the effective body (`base ⊕ override`), and materializes a `config_change(kind="alias")` via the existing `create_change`; an API with superadmin library CRUD (`require_org(TEMPLATE_MANAGE)`), tenant override upsert + apply/preview (`CONFIG_PUSH`). No new connector code — `apply_alias` already does the write.

**Tech Stack:** Python 3.14, FastAPI async, SQLAlchemy/Alembic (RLS), ARQ worker (reuses `apply_config_change`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m1-design.md`
**Branch:** `feat/config-templates-m1` (created; spec committed there).
**Scope:** Backend only. The frontend (superadmin Template Library page + the per-device Apply flow) is a separate plan after this merges.

**Run tests:** `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest <files> -q`. DB tests need env `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create:** `backend/app/models/config_template.py`, `backend/app/models/template_override.py`, `backend/migrations/versions/0019_config_templates.py`, `backend/app/services/templates.py`, `backend/app/schemas/templates.py`, `backend/app/api/templates.py`, `scripts/verify_template_live.py`, tests (`test_migration_0019.py`, `test_templates_service.py`, `test_templates_api.py`, `test_templates_rls_api.py`).
- **Modify:** `backend/app/core/rbac.py` (add `TEMPLATE_MANAGE` org-action), `backend/app/core/rls.py` (register `template_overrides` in `TENANT_TABLES`), `backend/app/models/config_change.py` (add `source_template_id`), `backend/app/main.py` (include the templates router).

---

## Task 1: RBAC action + models + migration 0019

**Files:** Modify `backend/app/core/rbac.py`, `backend/app/core/rls.py`, `backend/app/models/config_change.py`; Create `backend/app/models/config_template.py`, `backend/app/models/template_override.py`, `backend/migrations/versions/0019_config_templates.py`, `backend/tests/test_migration_0019.py`.

**Context:** `config_templates` is the GLOBAL MSP library — NOT tenant-scoped, NO RLS policy (write-gated to superadmin at the API layer). `template_overrides` IS tenant-scoped (mirror the `firmware_actions`/`config_changes` RLS pattern + register in `TENANT_TABLES`). Migration head is `0018` (verify). The RLS helpers live in `app/core/rls.py` (`policy_create_statement`, `POLICY_NAME`) and `app/core/db_roles.py` (`grant_app_role_statements`, `APP_ROLE`) — read `migrations/versions/0018_firmware_actions.py` as the template for the tenant-scoped table.

- [ ] **Step 1: Add the org-level action** in `backend/app/core/rbac.py`:
  - In `class Action(...)`, under the `# org-level (superadmin only)` group (next to `TENANT_MANAGE`/`USER_MANAGE`), add:
    ```python
    TEMPLATE_MANAGE = "template.manage"
    ```
  - Add it to the `_ORG_ACTIONS` set:
    ```python
    _ORG_ACTIONS = {Action.TENANT_MANAGE, Action.USER_MANAGE, Action.TEMPLATE_MANAGE}
    ```
  (Read the file first to match the exact current text. Do NOT add it to `_TENANT_MATRIX` — it's superadmin-only.)

- [ ] **Step 2: Create the global library model** `backend/app/models/config_template.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigTemplate(UUIDPKMixin, Base):
    """Global MSP template library row. NOT tenant-scoped (superadmin-managed)."""
    __tablename__ = "config_templates"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_config_templates_kind_name"),)

    kind: Mapped[str] = mapped_column(String)             # M1: "firewall_alias"
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    body: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 3: Create the per-tenant override model** `backend/app/models/template_override.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class TemplateOverride(UUIDPKMixin, Base):
    """Per-tenant customization (merge-patch) over a global template. Tenant-scoped (RLS)."""
    __tablename__ = "template_overrides"
    __table_args__ = (
        UniqueConstraint("template_id", "tenant_id", name="uq_template_overrides_template_tenant"),
    )

    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_templates.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    body_patch: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Add the tag column** to `backend/app/models/config_change.py` — add this field (e.g. right after `pre_apply_snapshot_id`):
```python
    source_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_templates.id", ondelete="SET NULL"), default=None
    )
```
(Read the file first; confirm `ForeignKey`, `UUID`, `Mapped`, `mapped_column` are already imported there — they are, since `device_id` uses them. Match the existing column style.)

- [ ] **Step 5: Register `template_overrides` for RLS** in `backend/app/core/rls.py` — append `"template_overrides"` to the `TENANT_TABLES` list. Do NOT add `config_templates` (it must stay global / un-policied).

- [ ] **Step 6: Register the models** so Alembic/metadata see them — in `backend/app/models/__init__.py`, mirror how `firmware_action`/`config_change` are imported (add `from app.models.config_template import ConfigTemplate  # noqa: F401` and `from app.models.template_override import TemplateOverride  # noqa: F401`, and to `__all__` if it lists names). Read the file to match the exact style; skip if it imports nothing explicit.

- [ ] **Step 7: Create migration** `backend/migrations/versions/0019_config_templates.py`. First confirm head: `cd backend && grep -E "^revision" migrations/versions/0018_firmware_actions.py` (expect `"0018"`). Read `0018_firmware_actions.py` to copy the EXACT RLS helper imports/calls. Then:
```python
"""config_templates (global) + template_overrides (RLS) + config_changes.source_template_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- global library: NO RLS policy (superadmin-gated at the API layer) ---
    op.create_table(
        "config_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "name", name="uq_config_templates_kind_name"),
    )

    # --- per-tenant override: tenant-scoped RLS ---
    op.create_table(
        "template_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body_patch", postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["config_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("template_id", "tenant_id", name="uq_template_overrides_template_tenant"),
    )
    op.create_index("ix_template_overrides_template_id", "template_overrides", ["template_id"])
    op.create_index("ix_template_overrides_tenant_id", "template_overrides", ["tenant_id"])
    op.execute("ALTER TABLE template_overrides ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE template_overrides FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("template_overrides"))

    # --- tag config_changes with its source template (nullable; history-preserving) ---
    op.add_column(
        "config_changes",
        sa.Column("source_template_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_config_changes_source_template", "config_changes", "config_templates",
        ["source_template_id"], ["id"], ondelete="SET NULL",
    )

    # grants on ALL tables incl. the two new ones (same as 0018)
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_config_changes_source_template", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "source_template_id")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON template_overrides")
    op.execute("ALTER TABLE template_overrides NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE template_overrides DISABLE ROW LEVEL SECURITY")
    op.drop_table("template_overrides")
    op.drop_table("config_templates")
```
NOTE: `config_templates` deliberately gets NO `ENABLE/FORCE ROW LEVEL SECURITY` and NO `policy_create_statement` — it is global. `grant_app_role_statements()` still grants the app role on it (so the API, running as `opngms_app`, can read/write it; write protection is RBAC-only). Confirm `grant_app_role_statements()` grants on ALL tables (it does in 0018) — if instead it takes a table list, pass both new tables.

- [ ] **Step 8: Create `backend/tests/test_migration_0019.py`:**
```python
from sqlalchemy import text

from app.core.rls import TENANT_TABLES


def test_overrides_in_tenant_tables_but_library_is_global():
    assert "template_overrides" in TENANT_TABLES        # RLS-managed
    assert "config_templates" not in TENANT_TABLES       # global, no tenant policy


async def test_tables_and_tag_column_exist(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('config_templates','template_overrides')"
        ))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='config_changes' AND column_name='source_template_id'"
        ))).scalars().all()
    assert "config_templates" in tables and "template_overrides" in tables
    assert cols == ["source_template_id"]
```

- [ ] **Step 9: Offline + DB verification.** Offline: `cd backend && .venv/bin/python -c "from app.models.config_template import ConfigTemplate; from app.models.template_override import TemplateOverride; print([c.name for c in ConfigTemplate.__table__.columns], [c.name for c in TemplateOverride.__table__.columns])"`. Revision chain: confirm `revision 0019 down_revision 0018`. Then run the migration test (the harness builds the schema via `Base.metadata.create_all` + `enable_rls_statements(TENANT_TABLES)`):
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_migration_0019.py -q
```
Also run a fresh end-to-end Alembic apply on a scratch DB to exercise the migration SQL (the `create_all` harness does NOT run the migration):
```bash
docker exec backend-db-1 psql -U opngms -d postgres -c "DROP DATABASE IF EXISTS opngms_migcheck3"
docker exec backend-db-1 psql -U opngms -d postgres -c "CREATE DATABASE opngms_migcheck3"
cd /home/l0rdg3x/coding/OPNGMS/backend && ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck3 .venv/bin/alembic upgrade head && ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck3 .venv/bin/alembic current
docker exec backend-db-1 psql -U opngms -d opngms_migcheck3 -c "SELECT relrowsecurity FROM pg_class WHERE relname IN ('config_templates','template_overrides') ORDER BY relname"
docker exec backend-db-1 psql -U opngms -d postgres -c "DROP DATABASE IF EXISTS opngms_migcheck3"
```
Expected: head=0019; `config_templates` relrowsecurity=`f` (global), `template_overrides` relrowsecurity=`t`. Always drop the scratch DB.

- [ ] **Step 10: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/core/rbac.py backend/app/core/rls.py backend/app/models/config_template.py backend/app/models/template_override.py backend/app/models/config_change.py backend/app/models/__init__.py backend/migrations/versions/0019_config_templates.py backend/tests/test_migration_0019.py
git commit -m "feat(templates): config_templates + template_overrides + source_template_id (migration 0019)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(Drop `__init__.py` from the add if you did not modify it.)

---

## Task 2: Templates service (validate + merge + materialize)

**Files:** Create `backend/app/services/templates.py`, `backend/tests/test_templates_service.py`.

**Context:** Pure-ish logic: validate a `firewall_alias` body, compute the effective body (`base ⊕ override`, shallow per-key, with `name`/`type` pinned to the base), and materialize a `config_change` via the existing `app.services.config_push.create_change(session, *, tenant_id, device_id, created_by, kind, operation, target, payload)`. `create_change` returns a draft `ConfigChange`; the caller (API) sets `source_template_id`/`status`/`scheduled_at` and enqueues.

- [ ] **Step 1: Write `backend/tests/test_templates_service.py`:**
```python
import pytest

from app.services.templates import (
    InvalidTemplateError,
    effective_body,
    validate_alias_body,
)


def test_validate_alias_body_ok():
    body = {"name": "web", "type": "host", "content": ["1.2.3.4"], "description": "x"}
    validate_alias_body(body)  # no raise


@pytest.mark.parametrize("bad", [
    {"type": "host", "content": ["1.2.3.4"]},               # missing name
    {"name": "", "type": "host", "content": ["1.2.3.4"]},    # empty name
    {"name": "web", "type": "host", "content": []},          # empty content
    {"name": "web", "type": "bogus", "content": ["1.2.3.4"]},# bad type
    {"name": "web", "type": "host", "content": "1.2.3.4"},   # content not a list
])
def test_validate_alias_body_rejects(bad):
    with pytest.raises(InvalidTemplateError):
        validate_alias_body(bad)


def test_effective_body_merges_patch_but_pins_name_and_type():
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "base"}
    patch = {"content": ["2.2.2.2", "3.3.3.3"], "description": "cust", "name": "HACK", "type": "url"}
    eff = effective_body("firewall_alias", base, patch)
    assert eff["content"] == ["2.2.2.2", "3.3.3.3"]   # patched
    assert eff["description"] == "cust"               # patched
    assert eff["name"] == "web" and eff["type"] == "host"  # pinned to base
    validate_alias_body(eff)


def test_effective_body_no_patch_returns_base():
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "base"}
    assert effective_body("firewall_alias", base, {}) == base
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_templates_service.py -q`
Expected: FAIL (ModuleNotFoundError: templates).

- [ ] **Step 3: Implement `backend/app/services/templates.py`:**
```python
"""Configuration-template engine (M1).

Validates the typed firmware/firewall body for a kind, computes the effective body
(base template merged with a per-tenant override patch), and materializes a config_change
that the existing config-push pipeline applies. M1 supports the `firewall_alias` kind only."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.services.config_push import create_change

ALIAS_TYPES = {"host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"}
_PINNED = ("name", "type")  # identity fields an override may not change


class InvalidTemplateError(ValueError):
    """A template/effective body failed validation."""


def validate_alias_body(body: dict) -> None:
    body = body or {}
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidTemplateError("alias 'name' is required")
    if body.get("type") not in ALIAS_TYPES:
        raise InvalidTemplateError(f"alias 'type' must be one of {sorted(ALIAS_TYPES)}")
    content = body.get("content")
    if not isinstance(content, list) or not content:
        raise InvalidTemplateError("alias 'content' must be a non-empty list")


_VALIDATORS = {"firewall_alias": validate_alias_body}


def validate_body(kind: str, body: dict) -> None:
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    validator(body)


def effective_body(kind: str, base: dict, patch: dict | None) -> dict:
    """Shallow per-key merge of base with the override patch; identity fields stay pinned to base."""
    merged = {**(base or {}), **(patch or {})}
    for key in _PINNED:
        if key in (base or {}):
            merged[key] = base[key]
    return merged


async def materialize_change(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID, created_by: uuid.UUID,
    template_id: uuid.UUID, kind: str, body: dict,
) -> ConfigChange:
    """Turn an effective `firewall_alias` body into a draft config_change (kind='alias', op='set')."""
    validate_body(kind, body)
    if kind != "firewall_alias":  # M1 maps only firewall_alias -> the config-push 'alias' kind
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind="alias", operation="set", target=body["name"], payload=body,
    )
    change.source_template_id = template_id
    await session.flush()
    return change
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_templates_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/templates.py backend/tests/test_templates_service.py
git commit -m "feat(templates): engine service (validate firewall_alias, effective body, materialize)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Schemas + API (library CRUD + override + apply/preview)

**Files:** Create `backend/app/schemas/templates.py`, `backend/app/api/templates.py`, `backend/tests/test_templates_api.py`, `backend/tests/test_templates_rls_api.py`; Modify `backend/app/main.py`.

**Context:** Read `app/api/config.py` (the config-push router: `require_tenant(Action.CONFIG_PUSH)`, `get_enqueuer`, `enforce_csrf`, `ctx.user.id`, the schedule endpoint's `enqueue("apply_config_change", str(change.id), defer_until=...)`) and `app/api/tenants.py` (`require_org(Action.TENANT_MANAGE)` for superadmin-only routes) and `app/core/deps.py` (`require_org` returns the `User`; `require_tenant` returns a `TenantContext` with `.user.id`). Mirror those exactly.

- [ ] **Step 1: Create `backend/app/schemas/templates.py`:**
```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class TemplateIn(BaseModel):
    kind: str = "firewall_alias"
    name: str
    description: str = ""
    body: dict = {}


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    body: dict | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    description: str
    body: dict
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OverrideIn(BaseModel):
    body_patch: dict = {}


class OverrideOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    body_patch: dict
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplyTemplateIn(BaseModel):
    scheduled_at: datetime | None = None


class TemplatePreviewOut(BaseModel):
    operation: str
    kind: str
    target: str
    new: dict
```

- [ ] **Step 2: Write `backend/tests/test_templates_api.py`** — mirror `test_config_push_api.py`'s helpers (`_seed_members` creating `ta@x.io` tenant_admin + `ro@x.io` read_only; `_insert_device`; `_override_enqueuer`; `_login`; `csrf_headers`). ADD a superadmin helper: read how `test`s authenticate a superadmin (search `backend/tests` for `is_superadmin` / a superadmin login helper — e.g. `make_user(s, ..., is_superadmin=True)` then `_login`). Then:
```python
# (imports + the copied _seed_members/_insert_device/_override_enqueuer/_login/csrf_headers helpers)
# plus a superadmin: create a user with is_superadmin=True and log in as them.

async def test_superadmin_can_create_and_list_template(api_client, db_engine):
    # superadmin creates a firewall_alias template in the global library
    ...
    r = await api_client.post("/api/templates", json={
        "kind": "firewall_alias", "name": "web-allow",
        "body": {"name": "web_allow", "type": "host", "content": ["1.2.3.4"]},
    }, headers=csrf_headers(api_client))
    assert r.status_code == 201
    assert r.json()["name"] == "web-allow"
    # any tenant user can LIST the library
    ...
    lst = await api_client.get("/api/templates")
    assert lst.status_code == 200 and any(t["name"] == "web-allow" for t in lst.json())


async def test_non_superadmin_cannot_write_library(api_client, db_engine):
    # a tenant_admin (not superadmin) is forbidden to create a library template
    ...
    r = await api_client.post("/api/templates", json={
        "kind": "firewall_alias", "name": "x",
        "body": {"name": "x", "type": "host", "content": ["1.1.1.1"]},
    }, headers=csrf_headers(api_client))
    assert r.status_code == 403


async def test_apply_template_enqueues_config_change(api_client, db_engine):
    # superadmin makes a template; a tenant operator applies it to their device -> a config_change is enqueued
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)
    template_id = await _seed_template(db_engine)  # insert a firewall_alias ConfigTemplate via the owner engine
    calls = _override_enqueuer()
    await _login(api_client, "ta@x.io")
    r = await api_client.post(
        f"/api/tenants/{tid}/devices/{did}/templates/{template_id}/apply",
        json={"scheduled_at": None}, headers=csrf_headers(api_client))
    assert r.status_code in (200, 201)
    assert len(calls) == 1
    name, args, defer_until = calls[0]
    assert name == "apply_config_change" and defer_until is None


async def test_apply_invalid_effective_body_is_422(api_client, db_engine):
    # an override that empties content -> invalid effective body -> 422, no enqueue
    ...
```
Write the helpers + the four tests fully, modeled on `test_config_push_api.py` (copy its exact fixtures and the superadmin-login mechanism you found). `_seed_template` inserts a `config_templates` row via the owner `db_engine` (raw SQL or the ORM). The apply path reuses the worker job name `apply_config_change` (already registered) — assert that name + the `defer_until`.

- [ ] **Step 3: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_templates_api.py -q`
Expected: FAIL (404 — router not wired).

- [ ] **Step 4: Create `backend/app/api/templates.py`:**
```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, get_current_user, require_org, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.config_template import ConfigTemplate
from app.models.device import Device
from app.models.template_override import TemplateOverride
from app.models.user import User
from app.schemas.templates import (
    ApplyTemplateIn, OverrideIn, OverrideOut, TemplateIn, TemplateOut,
    TemplatePreviewOut, TemplateUpdateIn,
)
from app.services.config_push import preview_change
from app.services.templates import InvalidTemplateError, effective_body, materialize_change, validate_body

router = APIRouter(prefix="/api", tags=["templates"])


# ---------- global library (superadmin-managed) ----------

@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(enforce_csrf)])
async def create_template(
    body: TemplateIn,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    try:
        validate_body(body.kind, body.body)
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    tpl = ConfigTemplate(kind=body.kind, name=body.name, description=body.description,
                         body=body.body, created_by=user.id)
    session.add(tpl)
    await session.flush()
    await session.commit()
    await session.refresh(tpl)
    return TemplateOut.model_validate(tpl)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TemplateOut]:
    # Any authenticated user may read the global library (needed to apply). It lives at /api/templates
    # with NO tenant_id in the path, so this uses get_current_user (not require_tenant, which binds a
    # tenant from the path) — the library is global, no tenant RLS to satisfy.
    rows = (await session.execute(
        select(ConfigTemplate).order_by(ConfigTemplate.kind, ConfigTemplate.name)
    )).scalars().all()
    return [TemplateOut.model_validate(r) for r in rows]


@router.put("/templates/{template_id}", response_model=TemplateOut,
            dependencies=[Depends(enforce_csrf)])
async def update_template(
    template_id: uuid.UUID, body: TemplateUpdateIn,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> TemplateOut:
    tpl = await session.get(ConfigTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.body is not None:
        try:
            validate_body(tpl.kind, body.body)
        except InvalidTemplateError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        tpl.body = body.body
    if body.name is not None:
        tpl.name = body.name
    if body.description is not None:
        tpl.description = body.description
    tpl.version += 1
    await session.commit()
    await session.refresh(tpl)
    return TemplateOut.model_validate(tpl)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(enforce_csrf)])
async def delete_template(
    template_id: uuid.UUID,
    user: User = Depends(require_org(Action.TEMPLATE_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    tpl = await session.get(ConfigTemplate, template_id)
    if tpl is not None:
        await session.delete(tpl)
        await session.commit()


# ---------- per-tenant override ----------

async def _template_or_404(session: AsyncSession, template_id: uuid.UUID) -> ConfigTemplate:
    tpl = await session.get(ConfigTemplate, template_id)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl


@router.put("/tenants/{tenant_id}/templates/{template_id}/override", response_model=OverrideOut,
            dependencies=[Depends(enforce_csrf)])
async def upsert_override(
    tenant_id: uuid.UUID, template_id: uuid.UUID, body: OverrideIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> OverrideOut:
    await _template_or_404(session, template_id)
    existing = (await session.execute(
        select(TemplateOverride).where(
            TemplateOverride.template_id == template_id, TemplateOverride.tenant_id == tenant_id)
    )).scalar_one_or_none()
    if existing is None:
        existing = TemplateOverride(template_id=template_id, tenant_id=tenant_id, body_patch=body.body_patch)
        session.add(existing)
    else:
        existing.body_patch = body.body_patch
    await session.flush()
    await session.commit()
    await session.refresh(existing)
    return OverrideOut.model_validate(existing)


async def _effective(session: AsyncSession, tenant_id: uuid.UUID, tpl: ConfigTemplate) -> dict:
    ov = (await session.execute(
        select(TemplateOverride).where(
            TemplateOverride.template_id == tpl.id, TemplateOverride.tenant_id == tenant_id)
    )).scalar_one_or_none()
    return effective_body(tpl.kind, tpl.body, ov.body_patch if ov else {})


# ---------- apply / preview (tenant) ----------

async def _device_or_404(session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview",
             response_model=TemplatePreviewOut, dependencies=[Depends(enforce_csrf)])
async def preview_template(
    tenant_id: uuid.UUID, device_id: uuid.UUID, template_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> TemplatePreviewOut:
    await _device_or_404(session, tenant_id, device_id)
    tpl = await _template_or_404(session, template_id)
    eff = await _effective(session, tenant_id, tpl)
    try:
        validate_body(tpl.kind, eff)
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TemplatePreviewOut(operation="set", kind="alias", target=eff["name"], new=eff)


@router.post("/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/apply",
             dependencies=[Depends(enforce_csrf)])
async def apply_template(
    tenant_id: uuid.UUID, device_id: uuid.UUID, template_id: uuid.UUID, body: ApplyTemplateIn,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> dict:
    await _device_or_404(session, tenant_id, device_id)
    tpl = await _template_or_404(session, template_id)
    eff = await _effective(session, tenant_id, tpl)
    try:
        change = await materialize_change(
            session, tenant_id=tenant_id, device_id=device_id, created_by=ctx.user.id,
            template_id=template_id, kind=tpl.kind, body=eff,
        )
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    change.status = "scheduled"
    change.scheduled_at = body.scheduled_at
    await session.flush()
    await enqueue("apply_config_change", str(change.id), defer_until=body.scheduled_at)
    await session.commit()
    return {"change_id": str(change.id), "status": "scheduled"}
```
IMPORTANT: confirm against `app/api/config.py` + `app/core/deps.py`: the exact `require_org`/`require_tenant` signatures, that `require_tenant(...)` yields a `TenantContext` with `.user.id`, that `require_org(...)` yields a `User`, the `get_enqueuer`/`enforce_csrf`/`get_session` import paths, and that the worker job name is `apply_config_change` with the `defer_until=` kwarg (config.py's schedule endpoint). Adjust if any differ.

- [ ] **Step 5: Wire the router** in `backend/app/main.py` — `from app.api import templates` + `app.include_router(templates.router)`, matching the existing include style.

- [ ] **Step 6: Write `backend/tests/test_templates_rls_api.py`** — mirror `test_firmware_rls_api.py`: (a) a static guard `assert "template_overrides" in TENANT_TABLES` and `assert "config_templates" not in TENANT_TABLES`; (b) an app-role cross-tenant isolation test proving tenant A cannot see tenant B's `template_overrides` row (seed two tenants + an override each via the owner engine, then query as `opngms_app` with tenant A's context → only A's row; no context → none). Copy the structure from `test_firmware_rls_api.py`; insert `template_overrides` rows with `(id, template_id, tenant_id, body_patch)` — you'll need a `config_templates` row first (insert one as the owner; it's global so no tenant context needed).

- [ ] **Step 7: Run the API + RLS tests + import sanity**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
.venv/bin/python -c "import app.api.templates, app.main; print('import OK')"
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_templates_api.py tests/test_templates_rls_api.py -q
```
Expected: import OK; all pass.

- [ ] **Step 8: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/templates.py backend/app/api/templates.py backend/app/main.py backend/tests/test_templates_api.py backend/tests/test_templates_rls_api.py
git commit -m "feat(templates): API — library CRUD (superadmin) + per-tenant override + apply/preview

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Live template-apply verify script

**Files:** Create `scripts/verify_template_live.py`.

**Context:** A dev tool (NOT in CI) proving the engine drives the real alias write end-to-end: build an effective `firewall_alias` body, materialize+apply it against the real box (reusing the connector's verified `apply_alias`), confirm the alias lands, then delete it (guaranteed cleanup). It exercises the SERVICE + connector directly (no HTTP/DB) to keep it self-contained, mirroring `scripts/verify_live_push.py`.

- [ ] **Step 1: Create `scripts/verify_template_live.py`** — READ `scripts/verify_live_push.py` first and mirror its credential parsing, `OpnsenseClient` construction (`verify_tls=False` for the self-signed box), and its add→confirm→delete-with-cleanup structure. Drive it through a `firewall_alias` effective body: `validate_alias_body(eff)` (from `app.services.templates`), then call the SAME connector write `verify_live_push.py` uses (`apply_alias` / the alias upsert) with the effective body, confirm via the alias read, and delete in a `finally`. Never print credentials. Print `ALL PASS` / `FAILED`.

- [ ] **Step 2: Parse-check**

Run: `cd /home/l0rdg3x/coding/OPNGMS && backend/.venv/bin/python -c "import ast; ast.parse(open('scripts/verify_template_live.py').read()); print('parse OK')"`
Expected: `parse OK`.

- [ ] **Step 3: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add scripts/verify_template_live.py
git commit -m "tools(templates): live engine→alias apply verify script (throwaway alias, cleanup)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **Orchestrator note:** after this task, the orchestrator runs the script against the real box and confirms the alias lands + cleanup, leaving the box clean. This proves the template engine's materialization drives the verified alias write on real hardware.

---

## Final verification

- [ ] Full backend suite green: `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`
- [ ] Migration 0019 applies cleanly via Alembic (`config_templates` no-RLS, `template_overrides` RLS) and reverses.
- [ ] Live template-apply (orchestrator) → `ALL PASS`, box left clean.
- [ ] Dispatch a final holistic review, then superpowers:finishing-a-development-branch. The frontend (superadmin Template Library page + per-device Apply flow) is a separate plan/PR.

---

## Self-Review (author)

**Spec coverage (backend portion):** `config_templates` global library + `TEMPLATE_MANAGE` superadmin gating (Task 1, 3); `template_overrides` tenant-RLS + `config_changes.source_template_id` tag (Task 1); the engine service — validate `firewall_alias`, effective `base ⊕ override` with pinned identity, materialize a `config_change(kind=alias)` (Task 2); API — superadmin CRUD + permissive tenant LIST/GET + tenant override upsert + apply/preview reusing the config-push pipeline (Task 3); RLS isolation + the global-library static guard (Task 1, 3); live proof on real hardware (Task 4). Profiles, extra kinds, and drift are explicitly out of scope (spec §3, M2/M3+).

**Placeholder scan:** the API/service/migration steps carry complete code; Task 3's test step names a concrete file to copy (`test_config_push_api.py`) and a concrete unknown to resolve (the superadmin-login helper) with a defined adaptation, not a vague TODO; Task 4 names `verify_live_push.py` as the exact mirror.

**Type consistency:** `validate_body`/`effective_body`/`materialize_change` (Task 2) are imported by the API (Task 3); `materialize_change` calls `create_change(...)` with the real signature and sets `source_template_id` (the column added in Task 1); the apply endpoint enqueues `apply_config_change` (the existing worker job) with `defer_until`; `TemplateIn/Out`, `OverrideIn/Out`, `ApplyTemplateIn`, `TemplatePreviewOut` are consistent across the API + tests; `TEMPLATE_MANAGE` is org-only (added to `_ORG_ACTIONS`, not `_TENANT_MATRIX`).

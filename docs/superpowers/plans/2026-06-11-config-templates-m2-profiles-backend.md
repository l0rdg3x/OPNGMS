# Configuration Templates — M2 (Profiles) Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backend for **profiles** — named, ordered bundles of library templates that apply to a device in one shot by fanning out to the (M1) per-template apply: one `config_change` per member, each `template ⊕ tenant-override`, tagged with both `source_template_id` and `source_profile_id`, enqueued through the existing config-push worker.

**Architecture:** Two new GLOBAL tables (`config_profiles`, `config_profile_members` — like `config_templates`, no tenant RLS) + a `config_changes.source_profile_id` tag; a `profiles` service that resolves ordered members and fans out to `services.templates.materialize_change`; an API with superadmin CRUD + member-set (`require_org(TEMPLATE_MANAGE)`) and tenant preview/apply (`CONFIG_PUSH`). No new connector code; reuses the M1 engine + config-push pipeline.

**Tech Stack:** Python 3.14, FastAPI async, SQLAlchemy/Alembic, ARQ worker (reuses `apply_config_change`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m2-profiles-design.md`
**Branch:** `feat/config-templates-m2-profiles` (created; spec committed).
**Scope:** Backend only. Frontend (profiles in the library UI + per-device apply-profile) is a separate plan after this merges.
**Reuse (M1, merged):** `app/models/config_template.py` (`ConfigTemplate`), `app/models/template_override.py`, `app/services/templates.py` (`validate_body`, `effective_body`, `materialize_change(session, *, tenant_id, device_id, created_by, template_id, kind, body)`), `app/api/templates.py` (the access patterns), `app/core/rbac.py` (`Action.TEMPLATE_MANAGE` org-action), migration `0019_config_templates.py` (the global-table + RLS helpers reference). `config_changes` already has `source_template_id`.

**Run tests:** `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest <files> -q`. DB tests need `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Create:** `backend/app/models/config_profile.py` (both models), `backend/migrations/versions/0020_config_profiles.py`, `backend/app/services/profiles.py`, `backend/app/schemas/profiles.py`, `backend/app/api/profiles.py`, `scripts/verify_profile_live.py`, tests (`test_migration_0020.py`, `test_profiles_service.py`, `test_profiles_api.py`).
- **Modify:** `backend/app/models/config_change.py` (add `source_profile_id`), `backend/app/main.py` (include the profiles router). `app/models/__init__.py` (register the two models).

---

## Task 1: Models + migration 0020

**Files:** Create `backend/app/models/config_profile.py`, `backend/migrations/versions/0020_config_profiles.py`, `backend/tests/test_migration_0020.py`; Modify `backend/app/models/config_change.py`, `backend/app/models/__init__.py`.

**Context:** `config_profiles` + `config_profile_members` are GLOBAL (like `config_templates`): NO `ENABLE/FORCE RLS`, NO policy, NOT in `TENANT_TABLES`. Migration head is `0019` (verify: `grep -E "^revision" backend/migrations/versions/0019_config_templates.py`). READ `0019_config_templates.py` for the exact `grant_app_role_statements()` usage (grants on ALL tables — covers the two new ones).

- [ ] **Step 1: Create `backend/app/models/config_profile.py`:**
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ConfigProfile(UUIDPKMixin, Base):
    """Global MSP profile: a named, ordered bundle of templates. NOT tenant-scoped."""
    __tablename__ = "config_profiles"
    __table_args__ = (UniqueConstraint("name", name="uq_config_profiles_name"),)

    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ConfigProfileMember(UUIDPKMixin, Base):
    """An ordered template membership of a profile. Global (MSP-defined)."""
    __tablename__ = "config_profile_members"
    __table_args__ = (
        UniqueConstraint("profile_id", "template_id", name="uq_profile_members_profile_template"),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_profiles.id", ondelete="CASCADE"), index=True
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_templates.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
```

- [ ] **Step 2: Add the tag column** to `backend/app/models/config_change.py` — after `source_template_id`:
```python
    source_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("config_profiles.id", ondelete="SET NULL"), default=None
    )
```
(READ the file; `ForeignKey`/`UUID`/`Mapped`/`mapped_column` are already imported — `source_template_id` uses them.)

- [ ] **Step 3: Register the models** in `backend/app/models/__init__.py` (mirror how `ConfigTemplate`/`TemplateOverride` are imported + listed in `__all__`): add `from app.models.config_profile import ConfigProfile, ConfigProfileMember  # noqa: F401`.

- [ ] **Step 4: Create migration** `backend/migrations/versions/0020_config_profiles.py` (revision "0020", down_revision "0019"). NO RLS on the two new tables (global). READ `0019_config_templates.py` to copy the exact `grant_app_role_statements()` import + loop.
```python
"""config_profiles + config_profile_members (global) + config_changes.source_profile_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import grant_app_role_statements

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "config_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_config_profiles_name"),
    )
    op.create_table(
        "config_profile_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["profile_id"], ["config_profiles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["config_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "template_id", name="uq_profile_members_profile_template"),
    )
    op.create_index("ix_config_profile_members_profile_id", "config_profile_members", ["profile_id"])
    op.create_index("ix_config_profile_members_template_id", "config_profile_members", ["template_id"])
    op.add_column(
        "config_changes",
        sa.Column("source_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_config_changes_source_profile", "config_changes", "config_profiles",
        ["source_profile_id"], ["id"], ondelete="SET NULL",
    )
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_config_changes_source_profile", "config_changes", type_="foreignkey")
    op.drop_column("config_changes", "source_profile_id")
    op.drop_table("config_profile_members")
    op.drop_table("config_profiles")
```

- [ ] **Step 5: Create `backend/tests/test_migration_0020.py`:**
```python
from sqlalchemy import text

from app.core.rls import TENANT_TABLES


def test_profiles_are_global_not_tenant_tables():
    assert "config_profiles" not in TENANT_TABLES
    assert "config_profile_members" not in TENANT_TABLES


async def test_profile_tables_and_tag_exist(db_engine):
    async with db_engine.connect() as conn:
        tables = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('config_profiles','config_profile_members')"
        ))).scalars().all()
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='config_changes' AND column_name='source_profile_id'"
        ))).scalars().all()
    assert set(tables) == {"config_profiles", "config_profile_members"}
    assert cols == ["source_profile_id"]
```

- [ ] **Step 6: Offline + DB verify.** Offline columns print (`from app.models.config_profile import ConfigProfile, ConfigProfileMember; print(...)`); revision chain `0020`/`0019`. Run the migration test (env vars). Then a fresh scratch-DB Alembic apply (mirror the M1 plan's pattern) on `opngms_migcheck5`: `alembic upgrade head` → head=0020; confirm `config_profiles`/`config_profile_members` relrowsecurity=`f` (global); `source_profile_id` on config_changes; downgrade -1 reverses; DROP the scratch DB always.

- [ ] **Step 7: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/models/config_profile.py backend/app/models/config_change.py backend/app/models/__init__.py backend/migrations/versions/0020_config_profiles.py backend/tests/test_migration_0020.py
git commit -m "feat(profiles): config_profiles + members + source_profile_id (migration 0020)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Profiles service (resolve members → fan-out materialize)

**Files:** Create `backend/app/services/profiles.py`, `backend/tests/test_profiles_service.py`.

**Context:** `materialize_profile(session, *, tenant_id, device_id, created_by, profile)` resolves the profile's ordered members and, for each, computes the effective body (reusing `services.templates`) and materializes a `config_change`, then tags it with `source_profile_id`. Validate-all-before-creating: if ANY member's effective body is invalid, raise `InvalidTemplateError` and create NOTHING. The per-tenant override is read from `template_overrides` (the function receives the session with the tenant GUC set by the API). Returns the ordered list of created `ConfigChange`s.

- [ ] **Step 1: Write `backend/tests/test_profiles_service.py`** — seed (owner engine) two `config_templates` + a `config_profiles` + two ordered `config_profile_members`; call `materialize_profile`; assert two `config_changes` created in order, each with `source_profile_id` set + the right `source_template_id`, kind `alias`. Add a case: a member whose effective body is invalid (e.g. a template with empty content via an override that empties it — or a template inserted with a body that fails validation) → `InvalidTemplateError` and zero `config_changes` created. Model the seeding on `backend/tests/test_templates_service.py` / `test_templates_api.py` (raw inserts via the owner `db_engine`). Use `two_tenants`/`db_engine` fixtures; insert a device for the tenant.

- [ ] **Step 2: Run → FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Implement `backend/app/services/profiles.py`:**
```python
"""Apply a profile = fan out to one config_change per member template, in order.

Reuses the M1 template engine (effective body + materialize) and the config-push pipeline.
Validation is atomic (a single invalid member fails the whole apply before anything is created);
the device-level apply of the produced changes is NOT atomic (each runs independently)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.models.config_profile import ConfigProfile, ConfigProfileMember
from app.models.config_template import ConfigTemplate
from app.models.template_override import TemplateOverride
from app.services.templates import InvalidTemplateError, effective_body, materialize_change, validate_body


async def _ordered_members(session: AsyncSession, profile_id: uuid.UUID) -> list[ConfigTemplate]:
    rows = (await session.execute(
        select(ConfigTemplate)
        .join(ConfigProfileMember, ConfigProfileMember.template_id == ConfigTemplate.id)
        .where(ConfigProfileMember.profile_id == profile_id)
        .order_by(ConfigProfileMember.position, ConfigProfileMember.id)
    )).scalars().all()
    return list(rows)


async def _effective(session: AsyncSession, tenant_id: uuid.UUID, tpl: ConfigTemplate) -> dict:
    ov = (await session.execute(
        select(TemplateOverride).where(
            TemplateOverride.template_id == tpl.id, TemplateOverride.tenant_id == tenant_id)
    )).scalar_one_or_none()
    return effective_body(tpl.kind, tpl.body, ov.body_patch if ov else {})


async def materialize_profile(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
    created_by: uuid.UUID, profile: ConfigProfile,
) -> list[ConfigChange]:
    """Validate ALL member effective bodies, then materialize one config_change per member (in order)."""
    templates = await _ordered_members(session, profile.id)
    if not templates:
        raise InvalidTemplateError("profile has no member templates")
    # 1) validate everything first (atomic validation; nothing created on a bad member)
    effective = []
    for tpl in templates:
        body = await _effective(session, tenant_id, tpl)
        validate_body(tpl.kind, body)
        effective.append((tpl, body))
    # 2) materialize one change per member, tag with the profile
    changes: list[ConfigChange] = []
    for tpl, body in effective:
        change = await materialize_change(
            session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
            template_id=tpl.id, kind=tpl.kind, body=body,
        )
        change.source_profile_id = profile.id
        changes.append(change)
    await session.flush()
    return changes
```

- [ ] **Step 4: Run → PASS.** Commit:
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/profiles.py backend/tests/test_profiles_service.py
git commit -m "feat(profiles): fan-out materialize service (ordered members, validate-all)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Schemas + API (profile CRUD + member-set + preview/apply)

**Files:** Create `backend/app/schemas/profiles.py`, `backend/app/api/profiles.py`, `backend/tests/test_profiles_api.py`; Modify `backend/app/main.py`.

**Context:** Mirror `app/api/templates.py` EXACTLY for the access wiring: superadmin library writes via `require_org(Action.TEMPLATE_MANAGE)`, any-auth LIST via `get_current_user`, tenant preview/apply via `require_tenant(Action.CONFIG_PUSH)` + `enforce_csrf` + `get_enqueuer`, `_device_or_404`, audit via `AuditService`. The apply endpoint fans out: `materialize_profile(...)` → set each change `status="scheduled"`/`scheduled_at` → commit → `enqueue("apply_config_change", str(c.id), defer_until=scheduled_at)` for EACH change (after commit). Preview returns the ordered list of per-member `TemplatePreviewOut`.

- [ ] **Step 1: Create `backend/app/schemas/profiles.py`:**
```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class ProfileIn(BaseModel):
    name: str
    description: str = ""
    template_ids: list[uuid.UUID] = []   # ordered member templates


class ProfileUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    template_ids: list[uuid.UUID] | None = None   # when present, replaces the ordered member set


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    version: int
    template_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class ApplyProfileIn(BaseModel):
    scheduled_at: datetime | None = None


class ProfileApplyOut(BaseModel):
    change_ids: list[uuid.UUID]
    status: str
```
(`ProfileOut.template_ids` is built from the ordered members. `TemplatePreviewOut` is reused from `app.schemas.templates`.)

- [ ] **Step 2: Write `backend/tests/test_profiles_api.py`** — mirror `test_templates_api.py`'s helpers (superadmin login `make_user(is_superadmin=True)` + `_login`; tenant `_seed_members`/`_insert_device`/`_override_enqueuer`/`_login`/`csrf_headers`; `_seed_template` to insert `config_templates`). Tests: superadmin creates a profile with two `template_ids` (members ordered) + lists it; non-superadmin write → 403; tenant applies the profile → TWO `apply_config_change` jobs enqueued; empty profile apply → 400; cross-tenant device → 404. Write fully, modeling on `test_templates_api.py`.

- [ ] **Step 3: Run → FAIL (404 router not wired).**

- [ ] **Step 4: Create `backend/app/api/profiles.py`** — READ `app/api/templates.py` and mirror the imports/deps. Endpoints (router prefix `/api`):
  - `POST /api/profiles` (201, `require_org(TEMPLATE_MANAGE)`, CSRF): create the profile + insert ordered `config_profile_members` from `body.template_ids` (validate each template_id exists → 422 if not); audit `profile.create`.
  - `GET /api/profiles` (`get_current_user`): list profiles, each with its ordered `template_ids`.
  - `PUT /api/profiles/{id}` (`require_org`, CSRF): update name/description; if `template_ids` present, REPLACE the member set (delete existing members, re-insert in order); bump `version`; audit `profile.update`.
  - `DELETE /api/profiles/{id}` (204, `require_org`, CSRF): delete (members CASCADE); audit `profile.delete`.
  - `POST /api/tenants/{tid}/devices/{did}/profiles/{id}/preview` (`require_tenant(CONFIG_PUSH)`, CSRF): load profile + ordered members → for each, the effective body → return `list[TemplatePreviewOut]` (operation "set", kind "alias", target=eff name, new=eff). Empty profile → 400.
  - `POST /api/tenants/{tid}/devices/{did}/profiles/{id}/apply` (`require_tenant(CONFIG_PUSH)`, CSRF, enqueue): `_device_or_404`; load the profile; `try: changes = await materialize_profile(...) except InvalidTemplateError → 400/422`; set each `change.status="scheduled"`, `scheduled_at`; `await session.commit()`; for each change `await enqueue("apply_config_change", str(change.id), defer_until=body.scheduled_at)`; audit `profile.apply` (details: profile id, change count); return `ProfileApplyOut(change_ids=[...], status="scheduled")`. **Enqueue AFTER commit** (like `templates.apply`). Empty profile (materialize raises) → 400.
  Use `status.HTTP_*` constants. Confirm `ctx.user.id`, `require_org` returns `User`, `require_tenant` returns `TenantContext`, the worker job name `apply_config_change`, against `app/api/templates.py`.

- [ ] **Step 5: Wire the router** in `backend/app/main.py` (`from app.api import profiles` + `app.include_router(profiles.router)`), matching the templates include.

- [ ] **Step 6: Run + import sanity:**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
.venv/bin/python -c "import app.api.profiles, app.main; print('import OK')"
TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest tests/test_profiles_api.py -q
```
Expected: import OK; all pass. Also run `tests/test_templates_api.py` (no enqueuer-leak regression).

- [ ] **Step 7: Commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/profiles.py backend/app/api/profiles.py backend/app/main.py backend/tests/test_profiles_api.py
git commit -m "feat(profiles): API — CRUD + member-set (superadmin) + per-device preview/apply fan-out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Live profile-apply verify script

**Files:** Create `scripts/verify_profile_live.py`.

**Context:** Dev tool (NOT CI) proving a 2-template profile fans out to two real alias writes. Mirror `scripts/verify_template_live.py`: build TWO effective `firewall_alias` bodies (two distinct throwaway alias names, list content), `validate_alias_body` each, apply BOTH via `apply_alias("add", ...)` (the engine's connector write), confirm both land with correct content, then delete both in a `finally` (guaranteed cleanup). Never print creds. `ALL PASS`/`FAILED`.

- [ ] **Step 1: Create `scripts/verify_profile_live.py`** mirroring `verify_template_live.py` but with two aliases (`opngms_profile_probe_a`/`_b`, contents `["192.0.2.71"]`/`["192.0.2.72","192.0.2.73"]`). Confirm both present + content; cleanup both.
- [ ] **Step 2: Parse + import check** (`ast.parse`; import `app.services.templates` + `OpnsenseClient`).
- [ ] **Step 3: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add scripts/verify_profile_live.py
git commit -m "tools(profiles): live profile fan-out verify (2 aliases, cleanup)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
> **Orchestrator note:** after this task, the orchestrator runs it against the real box (both aliases land + cleanup), proving the profile fan-out drives real writes.

---

## Final verification

- [ ] Full backend suite green: `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`
- [ ] Migration 0020 applies & reverses (both tables global, no RLS).
- [ ] Live profile verify (orchestrator) → `ALL PASS`, box clean.
- [ ] Final holistic review, then superpowers:finishing-a-development-branch. Frontend (profiles UI + apply-profile) is a separate plan.

---

## Self-Review (author)

**Spec coverage (backend):** `config_profiles` + ordered `config_profile_members` global tables + `config_changes.source_profile_id` (Task 1); the fan-out service — ordered resolution, validate-all-before-create, per-member materialize tagged with the profile, reusing M1's effective-body + materialize (Task 2); API — superadmin CRUD + member-set + any-auth LIST + tenant preview/apply-fan-out + audit (Task 3); live proof of the fan-out on real HW (Task 4). No new tenant-scoped table (per-tenant customization stays in M1's overrides); profiles are global, superadmin-gated. Profile-level overrides, persistent assignment, drift, and cross-member atomicity are out of scope (spec §3).

**Placeholder scan:** Tasks 1-2 carry complete code; Task 3's endpoints are specified behaviorally with the exact file to mirror (`api/templates.py`) + the concrete deps to confirm; Task 4 names `verify_template_live.py` as the exact mirror. The test steps name the concrete files to copy helpers from.

**Type consistency:** `materialize_profile` (Task 2) calls `materialize_change` with the M1 signature and sets `source_profile_id` (the column from Task 1); the API (Task 3) calls `materialize_profile` then enqueues `apply_config_change` (existing worker) per change; `ProfileIn/Out/UpdateIn`, `ApplyProfileIn`, `ProfileApplyOut` consistent across the API + tests; `TemplatePreviewOut` reused from M1; `TEMPLATE_MANAGE` reused (no new action).

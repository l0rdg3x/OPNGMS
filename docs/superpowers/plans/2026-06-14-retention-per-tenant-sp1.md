# Per-tenant retention SP-1 (Postgres stores) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Configurable retention with a **global default + per-tenant override** for the three Postgres-side stores behind dashboards/reports — `perimeter_attacker`, `events`, `metrics`. (The log lake is SP-2.)

**Architecture:** Global defaults live in the runtime-settings registry (env default + DB override). Per-tenant overrides live in a new RLS table `tenant_retention` (partial JSONB). A pure resolver merges `global < tenant`. The worker (owner connection) purges each store per tenant at its effective retention via a single `DELETE … USING (tenants ⋈ tenant_retention)` statement, replacing the native global TimescaleDB retention policies on `events`/`metrics`.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic (`backend/migrations/versions/`, next = 0038) / TimescaleDB / pytest. React 19 / Mantine v9 / Vite / 12-locale i18n.

**Spec:** `docs/superpowers/specs/2026-06-14-retention-per-tenant-sp1-design.md`

---

## File Structure

**PR1 — Foundation + perimeter (backend):**
- Modify: `backend/app/core/config.py` (3 Settings fields), `backend/app/services/runtime_settings.py` (3 registry keys), `backend/app/core/rls.py` (add `tenant_retention` to `TENANT_TABLES`), `backend/app/core/rbac.py` (add `RETENTION_CONFIG`), `backend/app/services/perimeter.py` (tenant-aware purge), `backend/app/worker.py` (purge job reads effective global), `backend/app/main.py` (mount retention router).
- Create: `backend/app/models/tenant_retention.py`, `backend/migrations/versions/0038_tenant_retention.py`, `backend/app/services/retention.py` (resolver + the shared purge helper), `backend/app/repositories/tenant_retention.py`, `backend/app/schemas/retention.py`, `backend/app/api/retention.py`, plus tests.

**PR2 — events + metrics (backend):** migration to remove native policies; `purge_events`/`purge_metrics` + cron; flip registry `active`. **PR3 — frontend.**

---

## PR1 — Foundation + per-tenant perimeter retention

Branch: `feat/retention-per-tenant` (already created, holds the spec).

### Task 1: Global defaults — Settings fields + registry keys

**Files:**
- Modify: `backend/app/core/config.py`, `backend/app/services/runtime_settings.py`
- Test: `backend/tests/test_runtime_settings.py` (existing)

- [ ] **Step 1: Add the Settings fields.** In `backend/app/core/config.py`, inside `class Settings`, add (near the other tunables):
```python
    perimeter_retention_days: int = 30   # per-tenant-overridable; worker purge reads the effective value
    events_retention_days: int = 90      # replaces the native TimescaleDB retention policy (PR2)
    metrics_retention_days: int = 30     # replaces the native TimescaleDB retention policy (PR2)
```

- [ ] **Step 2: Add the registry keys.** In `backend/app/services/runtime_settings.py`, append to `RUNTIME_SETTINGS`:
```python
    RuntimeSetting("perimeter_retention_days", int, lambda s: s.perimeter_retention_days, 1, 3650, "retention"),
    # events/metrics consumers (purge jobs) are wired in PR2 — keep inactive so the UI never shows a dead knob.
    RuntimeSetting("events_retention_days", int, lambda s: s.events_retention_days, 1, 3650, "retention", active=False),
    RuntimeSetting("metrics_retention_days", int, lambda s: s.metrics_retention_days, 1, 3650, "retention", active=False),
```

- [ ] **Step 3: Write the test.** In `backend/tests/test_runtime_settings.py` add:
```python
async def test_perimeter_retention_default_and_override(db_engine):
    from app.services.runtime_settings import runtime_defaults, _BY_KEY
    assert runtime_defaults()["perimeter_retention_days"] == 30
    assert _BY_KEY["perimeter_retention_days"].active is True
    assert _BY_KEY["events_retention_days"].active is False
```

- [ ] **Step 4: Run** `cd backend && python -m pytest tests/test_runtime_settings.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(retention): global retention defaults in the runtime registry`.

### Task 2: `tenant_retention` model + migration 0038 (table + RLS)

**Files:**
- Create: `backend/app/models/tenant_retention.py`, `backend/migrations/versions/0038_tenant_retention.py`
- Modify: `backend/app/core/rls.py` (TENANT_TABLES), `backend/app/models/__init__.py` (export)

- [ ] **Step 1: Model.** `backend/app/models/tenant_retention.py`:
```python
import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class TenantRetention(Base):
    __tablename__ = "tenant_retention"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    # Partial map: {"perimeter": N, "events": N, "metrics": N} (SP-2 adds "log_lake"). Absent => inherit global.
    overrides: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```
Export it in `backend/app/models/__init__.py` (add `"TenantRetention"` + the import, matching the existing style).

- [ ] **Step 2: Add to RLS list.** In `backend/app/core/rls.py`, add `"tenant_retention"` to `TENANT_TABLES`.

- [ ] **Step 3: Migration** `backend/migrations/versions/0038_tenant_retention.py` (mirror 0034):
```python
"""tenant_retention (per-tenant retention overrides, RLS)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_retention",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("overrides", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )
    op.execute("ALTER TABLE tenant_retention ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_retention FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("tenant_retention"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON tenant_retention FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON tenant_retention")
    op.execute("ALTER TABLE tenant_retention NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_retention DISABLE ROW LEVEL SECURITY")
    op.drop_table("tenant_retention")
```

- [ ] **Step 4: Verify** `cd backend && alembic upgrade head` on the test DB applies cleanly (or that the conftest schema build succeeds), then `python -m pytest tests/test_models.py -q` → PASS.
- [ ] **Step 5: Commit** `feat(retention): tenant_retention RLS table + migration 0038`.

### Task 3: The resolver

**Files:** Create `backend/app/services/retention.py`; Test `backend/tests/test_retention_resolver.py`

- [ ] **Step 1: Write the failing test.**
```python
from app.services.retention import effective_retention_days, RETENTION_STORES

def test_resolver_precedence():
    assert RETENTION_STORES == ("perimeter", "events", "metrics")
    assert effective_retention_days("perimeter", global_default=30, tenant_override=None) == 30
    assert effective_retention_days("perimeter", global_default=30, tenant_override={"perimeter": 7}) == 7
    # invalid / out-of-range / wrong-type overrides fall back to the global default
    for bad in ({"perimeter": 0}, {"perimeter": -1}, {"perimeter": 99999}, {"perimeter": "x"}, {"perimeter": True}):
        assert effective_retention_days("perimeter", global_default=30, tenant_override=bad) == 30
```

- [ ] **Step 2: Run → FAIL** (`python -m pytest tests/test_retention_resolver.py -q`).

- [ ] **Step 3: Implement** `backend/app/services/retention.py`:
```python
"""Per-tenant retention: the resolver (global default < per-tenant override) + the tenant-aware purge.

The purge runs in the worker as the DB owner (RLS-exempt — the only role that can drop TimescaleDB
retention policies and that sees every tenant). It is NEVER called on a user-facing path.
"""
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RETENTION_STORES = ("perimeter", "events", "metrics")  # SP-2 will add "log_lake"
_MIN, _MAX = 1, 3650


def effective_retention_days(store: str, *, global_default: int, tenant_override: dict | None) -> int:
    v = (tenant_override or {}).get(store)
    if isinstance(v, bool):  # bool is an int subclass — reject before the int check
        return global_default
    return v if isinstance(v, int) and _MIN <= v <= _MAX else global_default


async def _purge_table(session: AsyncSession, *, table: str, time_col: str, store: str,
                       now: datetime, global_default: int) -> int:
    """One statement: per-tenant cutoff from (tenants LEFT JOIN tenant_retention), clamped to [1,3650]."""
    stmt = text(f"""
        DELETE FROM {table} d
        USING (
            SELECT t.id AS tenant_id,
                   :now - make_interval(days => LEAST(:mx, GREATEST(:mn,
                       COALESCE(NULLIF(tr.overrides->>'{store}', '')::int, :gd)))) AS cutoff
            FROM tenants t
            LEFT JOIN tenant_retention tr ON tr.tenant_id = t.id
        ) c
        WHERE d.tenant_id = c.tenant_id AND d.{time_col} < c.cutoff
    """)
    res = await session.execute(stmt, {"now": now, "gd": global_default, "mn": _MIN, "mx": _MAX})
    return res.rowcount or 0
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(retention): resolver + shared tenant-aware purge helper`.

### Task 4: `TenantRetentionRepository`

**Files:** Create `backend/app/repositories/tenant_retention.py`; Test `backend/tests/test_tenant_retention_repo.py`

- [ ] **Step 1: Write the test** (mirror `ReportSettingsRepository`): get_or_default returns `{}`; upsert merges a partial patch; clearing a key (None) removes it.
```python
import uuid, pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.core.db import set_tenant_context
from app.repositories.tenant_retention import TenantRetentionRepository
from tests.factories import make_tenant

@pytest.fixture
def sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)

async def test_upsert_and_clear(sf):
    async with sf() as s:
        t = await make_tenant(s, slug="acme"); await s.commit(); tid = t.id
    async with sf() as s:
        await set_tenant_context(s, tid)
        repo = TenantRetentionRepository(s, tid)
        assert await repo.get_overrides() == {}
        await repo.upsert({"perimeter": 7, "events": 14}); await s.commit()
    async with sf() as s:
        await set_tenant_context(s, tid)
        repo = TenantRetentionRepository(s, tid)
        assert await repo.get_overrides() == {"perimeter": 7, "events": 14}
        await repo.upsert({"perimeter": None, "metrics": 5}); await s.commit()  # None clears perimeter
    async with sf() as s:
        await set_tenant_context(s, tid)
        assert await TenantRetentionRepository(s, tid).get_overrides() == {"events": 14, "metrics": 5}
```

- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `backend/app/repositories/tenant_retention.py`:
```python
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tenant_retention import TenantRetention


class TenantRetentionRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def _get(self) -> TenantRetention | None:
        return (await self.session.execute(
            select(TenantRetention).where(TenantRetention.tenant_id == self.tenant_id)
        )).scalar_one_or_none()

    async def get_overrides(self) -> dict:
        row = await self._get()
        return dict(row.overrides) if row else {}

    async def upsert(self, patch: dict) -> dict:
        """Merge `patch` into the stored overrides; a key set to None is removed (back to inherit)."""
        row = await self._get()
        if row is None:
            row = TenantRetention(tenant_id=self.tenant_id, overrides={})
            self.session.add(row)
        merged = {**row.overrides}
        for k, v in patch.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        row.overrides = merged
        await self.session.flush()
        return merged
```

- [ ] **Step 4: Run → PASS.** **Step 5: Commit** `feat(retention): tenant_retention repository`.

### Task 5: `RETENTION_CONFIG` action + per-tenant API

**Files:**
- Modify: `backend/app/core/rbac.py`, `backend/app/main.py`
- Create: `backend/app/schemas/retention.py`, `backend/app/api/retention.py`
- Test: `backend/tests/test_retention_api.py`

- [ ] **Step 1: Add the action.** In `backend/app/core/rbac.py`: add `RETENTION_CONFIG = "retention.config"` to the `Action` enum (tenant-level block) and to `_TENANT_MATRIX` as `Action.RETENTION_CONFIG: {TENANT_ADMIN}` (tenant_admin only; superadmin always allowed via `can()`).

- [ ] **Step 2: Schemas** `backend/app/schemas/retention.py`:
```python
from pydantic import BaseModel, Field, conint
from app.services.retention import RETENTION_STORES

class RetentionOut(BaseModel):
    overrides: dict[str, int]          # the stored per-tenant overrides
    defaults: dict[str, int]           # effective global defaults (for "inherit (N)" hints)

class RetentionPatch(BaseModel):
    # each store optional; an int sets an override, null clears it. Unknown keys rejected in the handler.
    values: dict[str, conint(ge=1, le=3650) | None] = Field(default_factory=dict)
```

- [ ] **Step 3: Write the failing tests** (`backend/tests/test_retention_api.py`): a `tenant_admin` can PUT/GET its overrides; a non-admin (`read_only`/`operator`) gets 403 on PUT; tenant A cannot see tenant B's overrides (RLS); an unknown store key or out-of-range value → 422; GET returns `defaults` reflecting the global registry. Reuse `make_user/make_tenant/make_membership` + a `POST /api/login` helper (see `tests/test_audit_gapfill.py`). Run → FAIL.

- [ ] **Step 4: Router** `backend/app/api/retention.py`:
```python
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.services.audit import AuditService
from app.services.retention import RETENTION_STORES
from app.services.runtime_settings import get_runtime_config
from app.repositories.tenant_retention import TenantRetentionRepository
from app.schemas.retention import RetentionOut, RetentionPatch

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["retention"])

async def _defaults(session: AsyncSession) -> dict[str, int]:
    cfg = await get_runtime_config(session)
    return {s: int(cfg[f"{s}_retention_days"]) for s in RETENTION_STORES}

@router.get("/retention", response_model=RetentionOut)
async def get_retention(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> RetentionOut:
    overrides = await TenantRetentionRepository(session, tenant_id).get_overrides()
    return RetentionOut(overrides=overrides, defaults=await _defaults(session))

@router.put("/retention", response_model=RetentionOut, dependencies=[Depends(enforce_csrf)])
async def put_retention(
    tenant_id: uuid.UUID, body: RetentionPatch, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.RETENTION_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> RetentionOut:
    unknown = sorted(k for k in body.values if k not in RETENTION_STORES)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown store(s): {', '.join(unknown)}")
    merged = await TenantRetentionRepository(session, tenant_id).upsert(dict(body.values))
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="tenant.retention.update",
        target_type="tenant_retention", target_id=str(tenant_id),
        ip=request.client.host if request.client else None, details={"keys": sorted(body.values)},
    )
    await session.commit()
    return RetentionOut(overrides=merged, defaults=await _defaults(session))
```
Mount in `backend/app/main.py`: `from app.api.retention import router as retention_router` + `app.include_router(retention_router)`.

- [ ] **Step 5: Run → PASS** (all of `test_retention_api.py`). **Step 6: Commit** `feat(retention): per-tenant retention API (RETENTION_CONFIG)`.

### Task 6: Tenant-aware perimeter purge

**Files:**
- Modify: `backend/app/services/perimeter.py`, `backend/app/worker.py`
- Test: `backend/tests/test_retention_purge_perimeter.py`

- [ ] **Step 1: Write the failing test.** Seed two tenants with `perimeter_attacker` rows at different ages; give tenant A a 7-day override (via the repo), leave tenant B on the global default (30). Run the purge with `global_default=30`. Assert: A's rows older than 7d are gone, A's newer rows stay; B's rows older than 30d gone, B's newer stay. (Seed rows directly with `set_tenant_context` + INSERT, mirroring `tests/test_perimeter_rls.py`.)

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Rewrite `purge_perimeter`** in `backend/app/services/perimeter.py` to delegate to the shared helper (drop the `RETENTION_DAYS` constant + the old blanket delete):
```python
from app.services.retention import _purge_table

async def purge_perimeter(session: AsyncSession, now: datetime, *, global_default: int) -> int:
    """Per-tenant retention sweep on the rollup (last_seen < each tenant's effective cutoff)."""
    return await _purge_table(session, table="perimeter_attacker", time_col="last_seen",
                              store="perimeter", now=now, global_default=global_default)
```
Update the worker job in `backend/app/worker.py` `purge_perimeter_attackers` to read the effective global from the runtime config:
```python
async def purge_perimeter_attackers(ctx: dict) -> str:
    factory = ctx["session_factory"]
    async with factory() as session:
        from app.services.runtime_settings import get_runtime_config
        gd = int((await get_runtime_config(session))["perimeter_retention_days"])
        n = await purge_perimeter(session, datetime.now(UTC), global_default=gd)
        await session.commit()
    return f"purged {n} stale perimeter rows"
```

- [ ] **Step 4: Run → PASS.** Then the **full suite**: `cd backend && python -m pytest -q` (single process) + `ruff check app/`. Report counts.
- [ ] **Step 5: Commit** `feat(retention): per-tenant perimeter purge`.

### Task 7: PR1 — open, green CI, squash-merge
- [ ] Push, open PR "feat(retention): per-tenant retention foundation + perimeter (SP-1 PR1)", green CI, squash-merge. Re-branch from updated main for PR2.

---

## PR2 — events + metrics (structured outline)

Branch from updated `main`: `feat/retention-ts`.

- **Migration 0039** (`backend/migrations/versions/`): `op.execute("SELECT remove_retention_policy('events', if_exists => true)")` + same for `metrics`. (Forward-only; the new purge jobs in this same PR take over — keep atomic.)
- **`backend/app/services/retention.py`**: add `purge_events` / `purge_metrics` thin wrappers over `_purge_table` (`table="events"/"metrics"`, `time_col="time"`, `store="events"/"metrics"`).
- **`backend/app/worker.py`**: a `purge_timeseries_retention` cron job (daily, e.g. reuse the 4:30 slot or a new one) that reads `events_retention_days`/`metrics_retention_days` from `get_runtime_config` and calls both purges; each wrapped independently (one failing must not block the other). Add to `WorkerSettings.functions` + `cron_jobs`.
- **`backend/app/services/runtime_settings.py`**: flip `events_retention_days` + `metrics_retention_days` to `active=True` (their consumers are now wired).
- **Tests** (`backend/tests/test_retention_purge_ts.py`): two tenants, per-tenant cutoffs, on both `events` and `metrics`; assert cross-tenant isolation + correct cutoff. Migration test: after 0039, `timescaledb_information.jobs` has no retention job for events/metrics. Full suite green.
- Open PR "feat(retention): per-tenant events + metrics retention (SP-1 PR2)", security note: purges run as owner in the worker (no user-facing path). Merge.

---

## PR3 — Frontend (structured outline)

Branch from updated `main`: `feat/retention-ui`.

- **`gen:api`** (after PR1+PR2 merged) so `/api/tenants/{id}/retention` is typed. Commit `schema.d.ts` + `openapi.json`.
- **Global group:** add `"retention"` to `GROUP_ORDER` in `frontend/src/admin/RuntimeSettingsSection.tsx`; add i18n `t.system.runtime.groups.retention` + `t.system.runtime.items.{perimeter,events,metrics}_retention_days` ({label, help}) across all 12 locales. The three knobs then auto-render (events/metrics appear once active from PR2).
- **Per-tenant card:** a `RetentionCard` on the tenant settings surface (mirror `frontend/src/pages/ReportSettingsPage.tsx`, which is per-active-tenant): a `useRetention()` hook (`api.GET/PUT "/api/tenants/{tenant_id}/retention"`), three `NumberInput`s each showing "Inherit global: N" (from `defaults`) with a clear-to-inherit affordance, gated to `tenant_admin`/superadmin. i18n keys for the card + `errors.retentionLoad`, all 12 locales.
- **Tests:** card renders inherit hints, saves an override, clears it; hook calls the right URL. Gate: `npm run build` + `npm test` + `npm run lint`.
- Open PR "feat(retention): retention settings UI — global group + per-tenant card (SP-1 PR3)". Merge.

---

## Release

- **Tag v0.11.0 + CHANGELOG** (SP-1): move `[Unreleased]` entries into `## [0.11.0]` + compare link. The release workflow derives the GitHub Release body from the section. (SP-2 — log lake per-tenant — will be a later minor.)

---

## Self-review notes
- **Spec coverage:** PR1 = foundation (registry + table + resolver + API) + perimeter (spec §"Data model" + perimeter enforcement). PR2 = events/metrics (spec §"Enforcement" — remove native policies). PR3 = UI (spec §"UI"). Release = v0.11.0. SP-2 (log lake) explicitly out.
- **`active` staging:** perimeter active in PR1; events/metrics inactive in PR1, flipped in PR2 — matches the registry rule (no dead knob). The per-tenant API already accepts only `RETENTION_STORES` keys; events/metrics overrides are storable from PR1 but only enforced from PR2 (acceptable — the value sits inert until its purge lands, same as the global knob).
- **Type consistency:** `RETENTION_STORES`, `effective_retention_days`, `_purge_table`, `TenantRetentionRepository`, `RETENTION_CONFIG`, `tenant_retention` table — one name each, used identically across tasks. Worker purge reads the effective global from `get_runtime_config` (DB override or env), then `_purge_table` applies per-tenant overrides — global<tenant precedence preserved end-to-end.
- **Disk caveat** documented in `retention.py`; PR2 removes the native policies in the same PR that adds the replacement jobs (no unbounded-growth window).

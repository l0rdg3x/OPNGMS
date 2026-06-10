# OPNGMS — Phase 5 / Milestone 5E: Scheduled Reports + Storage/History + UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every generated report, expose a tenant-scoped history + download, add an on-demand generate UI, and auto-generate a monthly report per tenant via an ARQ cron — completing Phase 5.

**Architecture:** New `generated_reports` table (tenant-scoped, RLS, stores PDF bytea + metadata) + repository; the `POST /reports` endpoint stores the result; `GET /reports` (list) + `GET /reports/{id}/download`; an ARQ job `generate_tenant_report` + a monthly cron `enqueue_scheduled_reports`; a frontend Reports page (generate + history + download). No new RBAC action (reuse `REPORT_GENERATE` for generate, `DEVICE_VIEW` for read).

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, Alembic, ARQ + Redis, FastAPI; React 19 + Mantine v9 + TanStack Query; pytest, Vitest/MSW.

---

## Context for the implementer (read first)

Codebase is **English**. 5A–5D in `main`.

- **Model/migration/RLS** patterns: `app/models/report_settings.py` + `migrations/versions/0011_report_settings.py` (created in 5D) are the closest template — copy the RLS block (ENABLE+FORCE+`policy_create_statement`+`grant_app_role_statements`), add the table to `TENANT_TABLES` in `app/core/rls.py`. `UUIDPKMixin` in `app/models/base.py`.
- **Worker** `app/worker.py`: jobs take `ctx` with `ctx["session_factory"]` (owner engine, RLS bypassed) and `ctx["redis"]`. `enqueue_X(ctx)` cron enumerates (owner) + `redis.enqueue_job("X", ...)`. `WorkerSettings.functions` + `cron_jobs` (`from arq import cron`). See `enqueue_config_backups`/`backup_device_config`. The worker may use `datetime.now(timezone.utc)` (normal Python).
- **Reporting** `app/services/reporting/service.py`: `ReportService(session, tenant_id).build_report(*, tenant_name, frm, to) -> bytes`. `app/api/reports.py` has `POST /reports` (`REPORT_GENERATE`+CSRF+audit) returning `Response(pdf, media_type="application/pdf")`, plus the settings endpoints; `require_tenant`, `enforce_csrf`, `AuditService`, `get_session` available.
- **Tenant model** `app/models/tenant.py` (`Tenant.id`, `Tenant.name`, `Tenant.status`).
- **Frontend**: typed client `src/api/client.ts` (binary: `api.GET(..., { parseAs: "blob" })` or direct `fetch`); `useTenant()` role; `src/i18n/en.ts`+`useT()`; `AppShell.tsx` nav+routes (the 5D `AppShellNav` shows role-gated links); `@mantine/dates` `DateTimePicker` (added in 4D-c); MSW tests. Browser download helper: create an object URL from the blob + a temporary `<a download>`.

**Commands**: backend pytest (see prior plans) + `alembic check`; frontend `npm test`/`build`/`lint`, `npm run gen:api`.

**Security:** RLS on `generated_reports`; list/download tenant-scoped (cross-tenant → 404). Worker generates as owner but the aggregator's explicit `tenant_id` filters scope each report. Generate `REPORT_GENERATE`+CSRF+audit; read `DEVICE_VIEW`. No secrets in reports.

---

## Task 1: Model + migration + RLS + repository

**Files:** Create `app/models/generated_report.py`, `migrations/versions/0012_generated_reports.py`, `app/repositories/generated_report.py`; Modify `app/models/__init__.py`, `app/core/rls.py`; Test `tests/test_generated_report_model.py`.

- [ ] **Step 1: Model** `app/models/generated_report.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class GeneratedReport(UUIDPKMixin, Base):
    __tablename__ = "generated_reports"
    __table_args__ = (
        Index("ix_generated_reports_tenant_created", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String)                 # 'on_demand' | 'scheduled'
    period_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_to: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```
Register in `app/models/__init__.py` (import + `__all__`).

- [ ] **Step 2: RLS list** — add `"generated_reports"` to `TENANT_TABLES` in `app/core/rls.py`.

- [ ] **Step 3: Migration** `migrations/versions/0012_generated_reports.py` (`revision="0012"`, `down_revision="0011"`): `op.create_table("generated_reports", ...)` (UUID PK `id`, `tenant_id` UUID NOT NULL + FK tenants CASCADE, `kind`/`period_from`/`period_to`/`created_by`(nullable)/`pdf`(LargeBinary)/`size`(Integer)/`created_at`), the index, then ENABLE+FORCE RLS + `policy_create_statement("generated_reports")` + `grant_app_role_statements()` — MIRROR `0011_report_settings.py` exactly. `downgrade()` reverses (REVOKE→DROP POLICY→NO FORCE→DISABLE→drop_table).

- [ ] **Step 4: Repository** `app/repositories/generated_report.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.generated_report import GeneratedReport

# Metadata columns (everything except the pdf bytes) for the list view.
_META = (
    GeneratedReport.id, GeneratedReport.kind, GeneratedReport.period_from,
    GeneratedReport.period_to, GeneratedReport.created_by, GeneratedReport.size,
    GeneratedReport.created_at,
)


class GeneratedReportRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def create(self, *, kind: str, period_from: datetime, period_to: datetime,
                     created_by: uuid.UUID | None, pdf: bytes) -> GeneratedReport:
        row = GeneratedReport(
            tenant_id=self.tenant_id, kind=kind, period_from=period_from, period_to=period_to,
            created_by=created_by, pdf=pdf, size=len(pdf),
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def list(self) -> list:
        # Metadata only (no pdf bytes), newest first.
        rows = (
            await self.session.execute(
                select(*_META).where(GeneratedReport.tenant_id == self.tenant_id)
                .order_by(GeneratedReport.created_at.desc())
            )
        ).all()
        return rows  # Row objects with .id/.kind/.period_from/.period_to/.created_by/.size/.created_at

    async def get(self, report_id: uuid.UUID) -> GeneratedReport | None:
        return (
            await self.session.execute(
                select(GeneratedReport).where(
                    GeneratedReport.tenant_id == self.tenant_id, GeneratedReport.id == report_id
                )
            )
        ).scalar_one_or_none()
```

- [ ] **Step 5: Model test** `tests/test_generated_report_model.py`: insert a row (owner session), read it back, assert `size == len(pdf)`, `kind`, defaults. Mirror `tests/test_report_settings_model.py`.

- [ ] **Step 6: Verify + commit** — full suite green; `alembic check` clean.
```bash
git add app/models/generated_report.py migrations/versions/0012_generated_reports.py \
        app/repositories/generated_report.py app/models/__init__.py app/core/rls.py \
        tests/test_generated_report_model.py
git commit -m "feat(reporting): generated_reports table + RLS + repository"
```

---

## Task 2: Worker — generate job + monthly cron

**Files:** Modify `app/worker.py`; Create `tests/test_worker_reports.py`.

- [ ] **Step 1: Write the failing tests** — `tests/test_worker_reports.py`:
  - `generate_tenant_report(ctx, tenant_id, frm, to, "scheduled")` with a seeded tenant + a device + some IDS events inserts a `generated_reports` row whose `pdf` starts with `%PDF-` and `kind=="scheduled"`, `created_by is None`. Build a fake `ctx = {"session_factory": <owner sessionmaker>}` (mirror `tests/test_worker_config.py` / `test_poller_e2e.py` for how `ctx` is faked).
  - `enqueue_scheduled_reports(ctx)` with two active tenants enqueues 2 jobs named `generate_tenant_report` with the prior-month range (assert the range = `[first_of_prev_month, first_of_this_month)`); use a fake `ctx["redis"]` recording `enqueue_job` calls (mirror `tests/test_worker_config.py`).
  Read `tests/test_worker_config.py` to copy the `ctx`/redis-fake pattern.

- [ ] **Step 2: Implement in `app/worker.py`**:
```python
async def generate_tenant_report(ctx: dict, tenant_id: str, frm: str, to: str, kind: str) -> str:
    """Job: build a report for a tenant + range and store it. Runs as owner; the aggregator's explicit
    tenant_id filters scope the data (RLS is bypassed for the owner, like the poller)."""
    from app.models.tenant import Tenant
    from app.repositories.generated_report import GeneratedReportRepository
    from app.services.reporting.service import ReportService

    factory = ctx["session_factory"]
    frm_dt, to_dt = datetime.fromisoformat(frm), datetime.fromisoformat(to)
    async with factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(tenant_id))
        if tenant is None:
            return "missing-tenant"
        pdf = await ReportService(session, uuid.UUID(tenant_id)).build_report(
            tenant_name=tenant.name, frm=frm_dt, to=to_dt
        )
        await GeneratedReportRepository(session, uuid.UUID(tenant_id)).create(
            kind=kind, period_from=frm_dt, period_to=to_dt, created_by=None, pdf=pdf
        )
        await session.commit()
        return "stored"


def _prior_month(now: datetime) -> tuple[datetime, datetime]:
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (first_this - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return prev_start, first_this


async def enqueue_scheduled_reports(ctx: dict) -> int:
    """Cron: enqueue a monthly report for every active tenant (prior calendar month)."""
    from app.models.tenant import Tenant

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    frm, to = _prior_month(datetime.now(timezone.utc))
    async with factory() as session:
        ids = (await session.execute(select(Tenant.id).where(Tenant.status == "active"))).scalars().all()
    for tid in ids:
        await redis.enqueue_job("generate_tenant_report", str(tid), frm.isoformat(), to.isoformat(), "scheduled")
    return len(ids)
```
Add `from datetime import timedelta` to the imports (alongside `datetime, timezone`). Register: add `generate_tenant_report` to `WorkerSettings.functions` and `cron(enqueue_scheduled_reports, day={1}, hour={4}, minute={0})` to `cron_jobs`.

- [ ] **Step 3: Run + commit**
```bash
git add app/worker.py tests/test_worker_reports.py
git commit -m "feat(reporting): scheduled-report worker job + monthly cron (prior month, per tenant)"
```

---

## Task 3: API — store on generate + list + download

**Files:** Modify `app/api/reports.py`; Create `app/schemas/generated_report.py`; Test `tests/test_generated_reports_api.py`.

- [ ] **Step 1: Schema** `app/schemas/generated_report.py`: `GeneratedReportOut { id: uuid; kind: str; period_from: datetime; period_to: datetime; created_by: uuid | None; size: int; created_at: datetime }`.

- [ ] **Step 2: Endpoints** in `app/api/reports.py`:
  - **Modify `POST /reports`**: after `pdf = await ReportService(...).build_report(...)`, store it:
    `await GeneratedReportRepository(session, tenant_id).create(kind="on_demand", period_from=payload.from_, period_to=payload.to, created_by=ctx.user.id, pdf=pdf)` before the audit/commit. Keep returning the PDF inline.
  - **`GET /reports`** (`require_tenant(Action.DEVICE_VIEW)`) → `list[GeneratedReportOut]` from `GeneratedReportRepository(session, tenant_id).list()` (map the Row objects to the schema).
  - **`GET /reports/{report_id}/download`** (`require_tenant(Action.DEVICE_VIEW)`) → `row = await repo.get(report_id)`; if None → `HTTPException(404)`; else `Response(content=row.pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="report-{report_id}.pdf"'})`.
  Import `GeneratedReportRepository`, `GeneratedReportOut`.

- [ ] **Step 3: Tests** `tests/test_generated_reports_api.py`: POST generate then `GET /reports` lists 1 row (kind on_demand, size>0, period matches); `GET /reports/{id}/download` returns `%PDF-`; a download of a random/cross-tenant id → 404; list requires auth (401) / works for any member (DEVICE_VIEW); cross-tenant isolation under `app_role_api_client` (tenant B can't list/download tenant A's report). Reuse helpers from `tests/test_report_api.py`.

- [ ] **Step 4: Commit**
```bash
git add app/schemas/generated_report.py app/api/reports.py tests/test_generated_reports_api.py
git commit -m "feat(reporting): store on generate + report history list + download API"
```

---

## Task 4: Frontend — Reports page (generate + history + download)

**Files:** Regen `src/api/schema.d.ts`; Create `src/reports/reportHooks.ts`, `src/pages/ReportsPage.tsx`, tests; Modify `src/components/AppShell.tsx`, `src/i18n/en.ts`.

- [ ] **Step 1: Schema + hooks** — `npm run gen:api`. `src/reports/reportHooks.ts`:
  - `useGeneratedReports()` — GET `/api/tenants/{tenant_id}/reports`, tenant-scoped (`["generated-reports", activeId]`).
  - `useGenerateReport()` — POST `/api/tenants/{tenant_id}/reports` body `{from, to}` requesting a blob; trigger a browser download of the returned PDF + invalidate the list. For the blob, use a direct `fetch` (`method: "POST"`, `credentials: "include"`, `X-OPNGMS-CSRF: "1"`, JSON body) → `res.blob()` → download helper; throw on `!res.ok`.
  - `downloadReport(activeId, id)` — `fetch` GET `/reports/{id}/download` (`credentials: "include"`) → blob → browser download.
  - Browser download helper: `const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = name; a.click(); URL.revokeObjectURL(url);`.
- [ ] **Step 2: Page** `src/pages/ReportsPage.tsx`: a generate form (`from`/`to` `DateTimePicker`, default last 30 days) + Generate button (shown only when role is `tenant_admin`/`operator` — `REPORT_GENERATE`), and a history `Table` (Period / Kind / Created / Size / Download per row). Read-only users see the history (DEVICE_VIEW) but not the generate form. Error → red notification. i18n.
- [ ] **Step 3: Nav + route** — in `AppShell.tsx` add a `NavLink to="/reports"` (label `t.nav.reports`, visible to all members) + `<Route path="/reports" element={<ReportsPage />} />`. i18n keys (`nav.reports`, `reports.history.*`, `reports.generate.*`).
- [ ] **Step 4: Tests** — Vitest/MSW: history list renders from a mocked GET; clicking Generate calls POST (mock returns a small blob); Download calls the download endpoint; read_only hides the generate form but shows history. (Mock `URL.createObjectURL`/`a.click` as needed — `vi.fn()`.)
- [ ] **Step 5: Verify + commit** — `npm test`/`build`/`lint` clean.
```bash
git add src/api/schema.d.ts openapi.json src/reports/reportHooks.ts src/pages/ReportsPage.tsx \
        src/components/AppShell.tsx src/i18n/en.ts src/pages/__tests__/reportspage.test.tsx
git commit -m "feat(fe): reports page (on-demand generate + history + download)"
```

---

## Task 5: Technical debt
- [ ] **Step 1: Append** 5E debt: PDF bytes in the DB (object store + retention/pruning later); fixed monthly cron (per-tenant schedules later); no email/delivery yet; history grows unbounded (retention later).
- [ ] **Step 2: Commit** `docs: technical debt milestone 5E`.

---

## Definition of "Done" (5E)
- On-demand and monthly-cron reports are **stored**; a member sees the tenant's report **history** and can
  **download** any report; the on-demand UI generates + downloads from the browser.
- Tenant-scoped + RLS (cross-tenant download → 404); generate gated by `REPORT_GENERATE`+CSRF+audit; read
  `DEVICE_VIEW`. Backend + frontend suites green; migration clean. **Phase 5 complete.**

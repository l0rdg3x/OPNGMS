# OPNGMS — Phase 5 / Milestone 5D: Per-Tenant White-Label Report Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a tenant admin configure per-tenant report branding (title, owner, timezone, **logo**) persisted in a `report_settings` table, applied to every generated report (logo on the title page via an inline `data:` URI; owner/timezone in the footer), driven by a settings UI.

**Architecture:** New `report_settings` table (one row per tenant, RLS) + repository; `ReportService`/`build_context` use the settings for branding and embed the logo as a `data:` URI; the report `url_fetcher` is updated to allow ONLY `data:` (block all network/file schemes). New `REPORT_CONFIG` RBAC action gates writes. A tenant-admin "Reports" settings page (form + logo upload) drives the API.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, Alembic, FastAPI (multipart upload), WeasyPrint; React 19 + Mantine v9 + TanStack Query + typed client; pytest, Vitest/MSW.

---

## Context for the implementer (read first)

Codebase is **English**. 5A–5C in `main`.

- **Model base** (`app/models/base.py`): `Base` (DeclarativeBase). Pattern: `class X(Base): __tablename__=...` with `Mapped[...] = mapped_column(...)`.
- **Migration pattern** (`migrations/versions/0010_config_changes.py`): `revision`/`down_revision`; `op.create_table(...)`; RLS via `from app.core.rls import POLICY_NAME, policy_create_statement` and `from app.core.db_roles import APP_ROLE, grant_app_role_statements`; execute `ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY`, the policy, and grants. Look at `0010` + `app/core/rls.py` (`enable_rls_statements`, `policy_create_statement`) and `0007_rls_metrics_alerts.py` for the exact helper calls.
- **RLS** (`app/core/rls.py`): `TENANT_TABLES` list — add `"report_settings"`. The migration must ENABLE+FORCE RLS + create the `tenant_isolation` policy + grant the app role, same as other tenant tables.
- **RBAC** (`app/core/rbac.py`): `Action` enum + `_TENANT_MATRIX`. Add `REPORT_CONFIG = "report.config"` granted to `{TENANT_ADMIN}` only.
- **Endpoint deps** (`app/core/deps.py`): `require_tenant(action)`, `enforce_csrf`, `get_session`, `TenantContext(tenant,user,role)`. Audit via `AuditService(session).record(...)`. FastAPI multipart: `from fastapi import UploadFile, File`.
- **Reporting engine** (`app/services/reporting/`): `service.py` has `_blocked_fetcher`, `html_to_pdf`, `ReportService(session, tenant_id).build_report(*, tenant_name, frm, to, timezone_name, owner)`. `context.py` `ReportContext(tenant_name, title, timezone, owner, range_from, range_to, sections)` + `build_context(aggregator, *, tenant_name, timezone_name, owner, frm, to, title=...)`. Template `templates/report.html.j2` title page. `api/reports.py` `POST /reports`.
- **Frontend**: `src/api/client.ts` (`api.GET/POST/PUT/DELETE`, CSRF auto on mutations, `credentials: include`); `src/components/AppShell.tsx` (nav links + `<Routes>`); `src/tenant/useTenant.ts` (`activeId`, `tenants` with `role`); `src/i18n/en.ts` + `useT()`; pages in `src/pages/`; tests `src/test/utils.tsx` + MSW. Mutation pattern in `src/components/DeviceActions.tsx`.

**Commands**: backend `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q` + `alembic upgrade head`/`alembic check`. Frontend `cd frontend && npm test && npm run build && npm run lint` (npm i may need `--legacy-peer-deps`). Regen types: `npm run gen:api`.

**Security (non-negotiable):**
- Logo: accept ONLY PNG (`\x89PNG\r\n`) / JPEG (`\xFF\xD8\xFF`) by **magic bytes**, size ≤ 512 KB; reject SVG/other → 400. Embed as a `data:` URI.
- `url_fetcher`: allow ONLY `data:` (delegate to `weasyprint.default_url_fetcher`), block everything else.
- Writes: `REPORT_CONFIG` + CSRF + audit. Reads: `DEVICE_VIEW`. Tenant-scoped + RLS. Title/owner autoescaped (never a URL attribute).

---

## Task 1: Model + migration + RLS + RBAC

**Files:** Create `app/models/report_settings.py`, `migrations/versions/0011_report_settings.py`; Modify `app/core/rls.py`, `app/core/rbac.py`; Test `tests/test_report_settings_model.py`.

- [ ] **Step 1: Model** — create `app/models/report_settings.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportSettings(Base):
    __tablename__ = "report_settings"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    title: Mapped[str] = mapped_column(String, default="Security & Activity Report",
                                       server_default="Security & Activity Report")
    owner: Mapped[str] = mapped_column(String, default="", server_default="")
    timezone: Mapped[str] = mapped_column(String, default="UTC", server_default="UTC")
    logo: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    logo_mime: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```
Import it where models are registered for `create_all` (tests). Check how other models are imported (e.g. `app/models/__init__.py` or wherever `Base.metadata` sees them) and add `ReportSettings` consistently.

- [ ] **Step 2: RLS list** — in `app/core/rls.py`, add `"report_settings"` to `TENANT_TABLES`.

- [ ] **Step 3: RBAC** — in `app/core/rbac.py`, add to `Action`: `REPORT_CONFIG = "report.config"`; to `_TENANT_MATRIX`: `Action.REPORT_CONFIG: {TENANT_ADMIN}`. Add a test asserting tenant_admin granted, operator+read_only denied, superadmin allowed (mirror the 5A `test_report_generate_grants`).

- [ ] **Step 4: Migration** — create `migrations/versions/0011_report_settings.py` (`down_revision="0010"`), mirroring `0010` + `0007`: `op.create_table("report_settings", ... columns from the model ...)` with `tenant_id` PK; then ENABLE + FORCE RLS, create the `tenant_isolation` policy (`policy_create_statement("report_settings")`), and grant the app role (`grant_app_role_statements` for the table). Read `0010_config_changes.py` and `app/core/rls.py` for the exact statement helpers and replicate them for this table. `downgrade()` drops the table.

- [ ] **Step 5: Model test** — `tests/test_report_settings_model.py`: insert a row (owner session), read it back, assert defaults (`title`, `timezone="UTC"`, `logo is None`). (Follow `tests/test_config_change_model.py` for style.)

- [ ] **Step 6: Verify + commit** — `alembic upgrade head` on the test DB works (conftest uses create_all, but run `alembic check` to ensure no drift); full suite green.
```bash
git add app/models/report_settings.py migrations/versions/0011_report_settings.py app/core/rls.py app/core/rbac.py tests/test_report_settings_model.py
# (+ the models registration file if you edited it)
git commit -m "feat(reporting): report_settings table + RLS + REPORT_CONFIG action"
```

---

## Task 2: Repository + engine wiring (settings branding + logo `data:` URI + data-only fetcher)

**Files:** Create `app/repositories/report_settings.py`; Modify `app/services/reporting/service.py`, `context.py`, `templates/report.html.j2`; Test `tests/test_report_settings_service.py`.

- [ ] **Step 1: Repository** — create `app/repositories/report_settings.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_settings import ReportSettings


class ReportSettingsRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def get(self) -> ReportSettings | None:
        return (
            await self.session.execute(
                select(ReportSettings).where(ReportSettings.tenant_id == self.tenant_id)
            )
        ).scalar_one_or_none()

    async def get_or_default(self) -> ReportSettings:
        row = await self.get()
        return row or ReportSettings(tenant_id=self.tenant_id)

    async def upsert(self, *, title: str, owner: str, timezone: str) -> ReportSettings:
        row = await self.get()
        if row is None:
            row = ReportSettings(tenant_id=self.tenant_id)
            self.session.add(row)
        row.title, row.owner, row.timezone = title, owner, timezone
        await self.session.flush()
        return row

    async def set_logo(self, logo: bytes, mime: str) -> None:
        row = await self.get()
        if row is None:
            row = ReportSettings(tenant_id=self.tenant_id)
            self.session.add(row)
        row.logo, row.logo_mime = logo, mime
        await self.session.flush()

    async def clear_logo(self) -> None:
        row = await self.get()
        if row is not None:
            row.logo, row.logo_mime = None, None
            await self.session.flush()
```

- [ ] **Step 2: Logo validation helper + data-URI** — add to `app/services/reporting/service.py` (top-level):
```python
import base64

MAX_LOGO_BYTES = 512 * 1024
_MAGIC = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg"}


def validate_logo(data: bytes) -> str:
    """Return the mime for an accepted PNG/JPEG (by magic bytes + size), else raise ValueError."""
    if len(data) > MAX_LOGO_BYTES:
        raise ValueError("logo too large (max 512 KB)")
    for magic, mime in _MAGIC.items():
        if data.startswith(magic):
            return mime
    raise ValueError("unsupported logo format (PNG or JPEG only)")


def logo_data_uri(data: bytes | None, mime: str | None) -> str | None:
    if not data or not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
```

- [ ] **Step 3: Data-only fetcher** — change `_blocked_fetcher` in `service.py` to allow `data:` only:
```python
from weasyprint import HTML, default_url_fetcher


def _report_url_fetcher(url: str):
    # Allow ONLY inline data: URIs (the embedded logo) — decoded inline, no network. Block every other
    # scheme (http/https/file/ftp) to prevent SSRF. WeasyPrint routes data: through the fetcher too.
    if url.startswith("data:"):
        return default_url_fetcher(url)
    raise ValueError(f"remote resource fetching is disabled in reports: {url!r}")
```
Update `html_to_pdf` to use `url_fetcher=_report_url_fetcher`. (Keep the old name as an alias if referenced by tests, or update the SSRF test which asserts a hostile http URL is not fetched.)

- [ ] **Step 4: Branding into context** — in `context.py`, add `logo_data_uri: str | None = None` to `ReportContext` (with the branding fields). In `templates/report.html.j2` title page, before/above the `<h1>`, add:
```jinja
    {% if ctx.logo_data_uri %}<img class="logo" src="{{ ctx.logo_data_uri }}" alt="logo" />{% endif %}
```
and a `.logo { max-height: 3cm; margin-bottom: .5cm; }` rule in `report.css`. (`ctx.logo_data_uri` is a server-built `data:` URI — safe; title/owner stay autoescaped.) Add a `logo_data_uri` param to `build_context` (default `None`) and pass it into the `ReportContext`.

- [ ] **Step 5: Service loads settings** — change `ReportService.build_report`/`build_html` to load settings and apply them. Signature becomes `build_report(*, frm, to, range only)`; internally:
```python
settings = await ReportSettingsRepository(self.session, self.tenant_id).get_or_default()
ctx_logo = logo_data_uri(settings.logo, settings.logo_mime)
# build_context(..., tenant_name=<tenant name>, title=settings.title, timezone_name=settings.timezone,
#               owner=settings.owner or None, logo_data_uri=ctx_logo, frm=frm, to=to)
```
Keep `tenant_name` sourced as today (the endpoint passes `ctx.tenant.name`); pass `title/owner/timezone` from settings. Update the endpoint (`api/reports.py`) to no longer pass `timezone_name`/`owner` (settings provide them) — pass only `tenant_name`, `frm`, `to`. Adjust `build_context`/`build_report` signatures accordingly and update existing tests that call them.

- [ ] **Step 6: Tests** — `tests/test_report_settings_service.py`: (a) with settings (title/owner/timezone + a real tiny PNG logo) the rendered HTML contains the title/owner and an `<img ... src="data:image/png;base64,`; (b) `validate_logo` accepts a PNG/JPEG magic, rejects SVG/oversize; (c) the `_report_url_fetcher` returns for a `data:` URL and raises for `http://...`; (d) `html_to_pdf` of an HTML embedding a valid PNG `data:` URI yields `%PDF-` (logo renders) while a hostile `http` img is dropped (still `%PDF-`). Update the existing `test_report_api.py` SSRF/generate tests for the new signatures.
- [ ] **Step 7: Commit**
```bash
git add app/repositories/report_settings.py app/services/reporting/service.py app/services/reporting/context.py \
        app/services/reporting/templates/report.html.j2 app/services/reporting/templates/report.css \
        app/api/reports.py tests/test_report_settings_service.py tests/test_report_api.py
git commit -m "feat(reporting): settings-driven branding + inline logo (data: URI) + data-only url_fetcher"
```

---

## Task 3: Settings API (get/update + logo upload/delete)

**Files:** Modify `app/api/reports.py`; Create `app/schemas/report_settings.py`; Test `tests/test_report_settings_api.py`.

- [ ] **Step 1: Schemas** — `app/schemas/report_settings.py`: `ReportSettingsIn { title: str; owner: str = ""; timezone: str = "UTC" }`, `ReportSettingsOut { title; owner; timezone; has_logo: bool; logo_mime: str | None }`.

- [ ] **Step 2: Endpoints** in `app/api/reports.py` (same router/prefix):
  - `GET /reports/settings` (`DEVICE_VIEW`) → `ReportSettingsOut` from `get_or_default()` (`has_logo = settings.logo is not None`).
  - `PUT /reports/settings` (`REPORT_CONFIG` + `enforce_csrf`) body `ReportSettingsIn` → `upsert(...)` → audit `report.settings.update` → commit → `ReportSettingsOut`.
  - `PUT /reports/settings/logo` (`REPORT_CONFIG` + `enforce_csrf`, `file: UploadFile = File(...)`) → read bytes, `validate_logo(data)` (→ 400 on ValueError), `set_logo(data, mime)` → audit `report.settings.logo` → commit → 204/`ReportSettingsOut`.
  - `DELETE /reports/settings/logo` (`REPORT_CONFIG` + `enforce_csrf`) → `clear_logo()` → audit → commit.
  - (Optional) `GET /reports/settings/logo` (`DEVICE_VIEW`) → `Response(content=settings.logo, media_type=settings.logo_mime)` or 404 if none — for the UI preview.
  Use the existing endpoint patterns (`require_tenant`, `enforce_csrf`, `AuditService`, `await session.commit()`). Wrap `validate_logo` ValueError → `HTTPException(400)`.

- [ ] **Step 3: Tests** — `tests/test_report_settings_api.py`: get defaults; put updates (then get reflects them); a non-admin (operator) PUT → 403; logo upload of a valid PNG → `has_logo True`; upload of a non-image (e.g. `b"<svg>"`) → 400; CSRF missing → 403; cross-tenant isolation (a settings row for tenant A invisible to tenant B under `app_role_api_client`). Mirror `tests/test_config_push_api.py`/`test_report_api.py` helpers (`_login_superadmin`, CSRF dict, membership for role tests).

- [ ] **Step 4: Commit**
```bash
git add app/schemas/report_settings.py app/api/reports.py tests/test_report_settings_api.py
git commit -m "feat(reporting): report settings API (get/update + logo upload/delete), RBAC+CSRF+audit"
```

---

## Task 4: Frontend — Reports settings page (tenant_admin)

**Files:** Regen `src/api/schema.d.ts`; Create `src/reports/settingsHooks.ts`, `src/pages/ReportSettingsPage.tsx`, tests; Modify `src/components/AppShell.tsx` (nav + route), `src/i18n/en.ts`.

- [ ] **Step 1: Schema + hooks** — `npm run gen:api`. Create `src/reports/settingsHooks.ts`: `useReportSettings()` (GET, tenant-scoped on `activeId`), `useUpdateReportSettings()` (PUT → invalidate), `useUploadLogo()` / `useDeleteLogo()`. For the multipart upload, since openapi-fetch typing for files is awkward, use a direct `fetch` to `${VITE_API_BASE ?? ""}/api/tenants/${activeId}/reports/settings/logo` with `method: "PUT"`, `credentials: "include"`, header `X-OPNGMS-CSRF: "1"`, `body: formData` (FormData with `file`); throw on `!res.ok`; invalidate on success. (Mirror the JSON hooks in `src/config/changeHooks.ts` for GET/PUT.)

- [ ] **Step 2: Page** — `src/pages/ReportSettingsPage.tsx`: a Mantine form (`title` TextInput, `owner` TextInput, `timezone` TextInput/Select) bound to `useReportSettings`/`useUpdateReportSettings`, plus a `FileInput`/`Dropzone` for the logo (accept `image/png,image/jpeg`) → `useUploadLogo`, a logo preview (from the `GET .../logo` endpoint or the just-uploaded file) + a "Remove logo" button → `useDeleteLogo`. **Visible only to `tenant_admin`** (role from `useTenant()`); show an `Alert` "Admins only" otherwise. On a 403 from a write, show a red notification. All strings via i18n.

- [ ] **Step 3: Nav + route** — in `src/components/AppShell.tsx`, add a nav `NavLink to="/reports/settings"` (label `t.nav.reportSettings`) shown when the active tenant role is `tenant_admin`, and a `<Route path="/reports/settings" element={<ReportSettingsPage />} />`. Add i18n keys (`nav.reportSettings`, a `reports.settings.*` group: title/owner/timezone/logo/upload/remove/save/adminsOnly + errors).

- [ ] **Step 4: Tests** — Vitest + MSW: the page renders current settings; saving calls PUT with the form values; uploading a logo calls the logo PUT; read_only/operator sees the "Admins only" state (no form); a 403 surfaces. Use `renderWithProviders` + a `withTenant` helper (role param).

- [ ] **Step 5: Verify + commit** — `npm test`, `npm run build`, `npm run lint` clean.
```bash
git add src/api/schema.d.ts openapi.json src/reports/settingsHooks.ts src/pages/ReportSettingsPage.tsx \
        src/components/AppShell.tsx src/i18n/en.ts src/pages/__tests__/reportsettings.test.tsx
git commit -m "feat(fe): report settings page (white-label: title/owner/timezone/logo), tenant_admin"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Append** the 5D debt: logo stored in DB (fine at this scale; an object store later); SVG logos rejected (safety); timezone is a free string (a validated IANA picker later); no per-template themes yet.
- [ ] **Step 2: Commit** `docs: technical debt milestone 5D`.

---

## Definition of "Done" (5D)
- A tenant admin sets title/owner/timezone + uploads a PNG/JPEG logo; generated reports show the logo on
  the title page and owner/timezone in the footer (from settings).
- Logo validated by magic bytes + size, embedded as an inline `data:` URI; the report fetcher allows only
  `data:` (no SSRF). Writes gated by `REPORT_CONFIG` + CSRF + audited; reads `DEVICE_VIEW`; tenant-scoped + RLS.
- Backend + frontend suites green; `alembic` migration applies cleanly.

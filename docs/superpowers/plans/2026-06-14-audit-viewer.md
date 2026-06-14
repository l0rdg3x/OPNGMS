# Audit Viewer (superadmin) + complete audit coverage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give superadmins a global, filterable, exportable Audit viewer over the existing `audit_log` ledger, and make audit coverage of mutating actions complete + regression-proof.

**Architecture:** The write-only `audit_log` table already exists. We (B) fill the unaudited mutating routes and add a CI guard test that fails if any mutating route lacks an audit call; then (A) add a superadmin-only read API (`GET /api/admin/audit` + `export.csv`) with actor/tenant enrichment and an Audit page in the SPA. No model change beyond one supporting index.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic (dir `backend/migrations/versions/`) / pytest. React 19 / TypeScript / Mantine v9 / Vite / 12-locale i18n.

**Spec:** `docs/superpowers/specs/2026-06-14-audit-viewer-design.md`

---

## File Structure

**PR1 — Coverage (backend):**
- Modify: `backend/app/api/firmware.py` (add `device.firmware.action` record), `backend/app/api/setup.py` (add `setup.bootstrap`), `backend/app/api/report_schedules.py` (add `report.schedule.send_now`), possibly `backend/app/api/mfa.py` (classify `/me/mfa/setup`).
- Create: `backend/tests/test_audit_coverage.py` (the guard), `backend/tests/test_audit_gapfill.py` (per-route record tests).

**PR2 — Read API (backend):**
- Modify: `backend/app/core/rbac.py` (relocate `AUDIT_VIEW` to org-level), `backend/app/main.py` (mount router).
- Create: `backend/app/schemas/audit.py`, `backend/app/repositories/audit.py`, `backend/app/api/audit.py`, `backend/migrations/versions/0037_audit_log_action_ts_index.py`, `backend/tests/test_audit_api.py`.

**PR3 — Viewer (frontend):**
- Create: `frontend/src/pages/AuditPage.tsx`, `frontend/src/audit/auditHooks.ts`, `frontend/src/pages/__tests__/AuditPage.test.tsx`.
- Modify: the nav component (add superadmin-only `/audit` entry), the router (register `/audit`), `frontend/src/i18n/en.ts` + 11 locale files, `frontend/src/api/schema.d.ts` (via `npm run gen:api`).

---

## PR1 — Complete audit coverage (build first; ships value alone)

Branch: `feat/audit-viewer` (already created, holds the spec).

### Task 1: Gap-fill `firmware/action` (the high-impact gap)

**Files:**
- Modify: `backend/app/api/firmware.py:57-84`
- Test: `backend/tests/test_audit_gapfill.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_audit_gapfill.py
import uuid
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models.audit import AuditLog
from tests.factories import make_user, make_tenant, make_membership


async def _login(api_client, session_factory, *, email="sa@test.com"):
    """Create a superadmin + a tenant with a device, log the client in, return (tenant_id, device_id)."""
    async with session_factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure", is_superadmin=True)
        tenant = await make_tenant(s, slug="acme")
        await make_membership(s, user_id=user.id, tenant_id=tenant.id, role="tenant_admin")
        await s.commit()
        tenant_id = tenant.id
    # seed a device for that tenant (owner session, set RLS context)
    from app.core.rls import set_tenant_context
    device_id = uuid.uuid4()
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        await s.execute(
            __import__("sqlalchemy").text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags) VALUES (:id,:t,'fw','https://fw',''::bytea,''::bytea,"
                "true,'unverified','{}')"
            ),
            {"id": device_id, "t": tenant_id},
        )
        await s.commit()
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200
    return tenant_id, device_id


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def test_firmware_action_writes_audit(api_client, session_factory):
    tenant_id, device_id = await _login(api_client, session_factory)
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/firmware/action",
        json={"kind": "firmware_update"},
        headers={"X-OPNGMS-CSRF": csrf},
    )
    assert r.status_code == 201
    async with session_factory() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "device.firmware.action"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_id == str(device_id)
    assert rows[0].details.get("kind") == "firmware_update"
```

- [ ] **Step 2: Run it; expect FAIL** (no audit row recorded yet)

Run: `cd backend && python -m pytest tests/test_audit_gapfill.py::test_firmware_action_writes_audit -q`
Expected: FAIL — `assert len(rows) == 1` gets 0.

- [ ] **Step 3: Add the audit record in the route**

In `backend/app/api/firmware.py`, add the import and a `request: Request` param, and record before commit. Add to imports:
```python
from fastapi import Request
from app.services.audit import AuditService
```
Add `request: Request,` to the `create_firmware_action` signature (after `body: FirmwareActionIn,`), and before `await session.commit()` (line 82):
```python
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="device.firmware.action",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None,
        details={"kind": body.kind, "target": body.target, "scheduled_at": str(body.scheduled_at) if body.scheduled_at else None},
    )
```

- [ ] **Step 4: Run it; expect PASS**

Run: `cd backend && python -m pytest tests/test_audit_gapfill.py::test_firmware_action_writes_audit -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/firmware.py backend/tests/test_audit_gapfill.py
git commit -m "feat(audit): record device.firmware.action at request time"
```

### Task 2: Gap-fill `POST /setup` (first-superadmin bootstrap)

**Files:**
- Modify: `backend/app/api/setup.py`
- Test: `backend/tests/test_audit_gapfill.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_setup_writes_audit(api_client, session_factory):
    r = await api_client.post(
        "/api/setup",
        json={"email": "first@admin.io", "name": "First", "password": "pw12345-secure"},
    )
    assert r.status_code == 201
    async with session_factory() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "setup.bootstrap"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id is None
    assert rows[0].details.get("email") == "first@admin.io"
```

- [ ] **Step 2: Run it; expect FAIL.**

Run: `cd backend && python -m pytest tests/test_audit_gapfill.py::test_setup_writes_audit -q`

- [ ] **Step 3: Record in `setup.py`**

Add a `request: Request` param and the record after `await repo.add(user)` (so `user.id` is populated) and before `await session.commit()`:
```python
from fastapi import Request          # add to imports
from app.services.audit import AuditService
# ...
async def setup(payload: SetupIn, request: Request, session: AsyncSession = Depends(get_session)) -> User:
    # ... existing body up to await repo.add(user) ...
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="setup.bootstrap",
        target_type="user", target_id=str(user.id),
        ip=request.client.host if request.client else None,
        details={"email": payload.email},
    )
    await session.commit()
    return user
```
(`repo.add` flushes; if unsure, call `await session.flush()` before `.record` so `user.id` exists.)

- [ ] **Step 4: Run it; expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/setup.py backend/tests/test_audit_gapfill.py
git commit -m "feat(audit): record setup.bootstrap on first-superadmin creation"
```

### Task 3: Gap-fill `report-schedules /send-now`

**Files:**
- Modify: `backend/app/api/report_schedules.py:93-106`
- Test: `backend/tests/test_audit_gapfill.py`

- [ ] **Step 1: Write the failing test** (log in as in Task 1; create a schedule row directly, then POST send-now)

```python
async def test_send_now_writes_audit(api_client, session_factory):
    tenant_id, _ = await _login(api_client, session_factory, email="sa2@test.com")
    sched_id = uuid.uuid4()
    from app.core.rls import set_tenant_context
    import sqlalchemy as sa
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        await s.execute(sa.text(
            "INSERT INTO report_schedules (id, tenant_id, cadence, recipients, sections, enabled) "
            "VALUES (:id,:t,'weekly','{}','{}',true)"), {"id": sched_id, "t": tenant_id})
        await s.commit()
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post(f"/api/tenants/{tenant_id}/report-schedules/{sched_id}/send-now",
                              headers={"X-OPNGMS-CSRF": csrf})
    assert r.status_code == 202
    async with session_factory() as s:
        rows = (await s.execute(select(AuditLog).where(AuditLog.action == "report.schedule.send_now"))).scalars().all()
    assert len(rows) == 1 and rows[0].target_id == str(sched_id)
```
*(Verify the `report_schedules` columns at execution — adjust the INSERT to the real schema; the route mount prefix is `/api/tenants/{tenant_id}/report-schedules`.)*

- [ ] **Step 2: Run it; expect FAIL.**
- [ ] **Step 3: Record in `send_now`** — add `request: Request` param and, after the `enqueue(...)` and before returning:
```python
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.send_now",
        target_type="report_schedule", target_id=str(schedule_id),
        ip=request.client.host if request.client else None, details={},
    )
    await session.commit()
```
(Confirm `AuditService` is imported in this file — it already is, per the delete route.)
- [ ] **Step 4: Run it; expect PASS.**
- [ ] **Step 5: Commit**
```bash
git add backend/app/api/report_schedules.py backend/tests/test_audit_gapfill.py
git commit -m "feat(audit): record report.schedule.send_now"
```

### Task 4: Classify `POST /me/mfa/setup`

**Files:**
- Read: `backend/app/api/mfa.py:60-80`
- Modify: `backend/app/api/mfa.py` **or** the EXEMPT allowlist in Task 5.

- [ ] **Step 1:** Read the `/me/mfa/setup` handler. Decide:
  - If it **persists** anything (e.g., a pending TOTP secret to `user_mfa`) → add `await AuditService(session).record(..., action="mfa.setup_start", target_type="user", target_id=str(user.id), ...)` and a record test mirroring Task 1.
  - If it only **generates** a secret/URI in-memory and persists nothing until `/confirm` → it is a read-shaped POST: add it to the EXEMPT allowlist in Task 5 with the reason `"mfa setup-start: nothing persisted until confirm"`.
- [ ] **Step 2:** Implement the chosen branch; if it's an audit add, write the failing test first (TDD), then the record, then pass. If exempt, no code here (handled in Task 5).
- [ ] **Step 3: Commit** (if changed)
```bash
git add backend/app/api/mfa.py backend/tests/test_audit_gapfill.py
git commit -m "feat(audit): classify mfa setup-start (audit|exempt)"
```

### Task 5: The CI guard test — every mutating route audits or is allowlisted

**Files:**
- Create: `backend/tests/test_audit_coverage.py`

- [ ] **Step 1: Write the test** (it encodes the policy; it will fail first if any gap remains)

```python
# backend/tests/test_audit_coverage.py
import inspect
from fastapi.routing import APIRoute
from app.main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Reads performed via POST (carry a body) — genuinely no state change, so no audit expected.
EXEMPT = {
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/firmware/check"),
    ("POST", "/api/tenants/{tenant_id}/logs/search"),  # verify exact path at execution
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview"),
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/preview"),
    # ("POST", "/api/me/mfa/setup"),  # uncomment only if Task 4 classified it exempt
}
# Routes that audit inside a service they call, not inline — explicit so it's a reviewed choice.
AUDITED_INDIRECT: set = set()


def _audits_inline(endpoint) -> bool:
    try:
        src = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    return ".record(" in src


def test_every_mutating_route_is_audited_or_allowlisted():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods & MUTATING
        if not methods:
            continue
        for m in methods:
            key = (m, route.path)
            if key in EXEMPT or key in AUDITED_INDIRECT:
                continue
            if not _audits_inline(route.endpoint):
                missing.append(key)
    assert not missing, (
        "Mutating routes with no audit.record() and not allowlisted:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(missing))
        + "\nAdd an AuditService(...).record(...) call, or add to EXEMPT/AUDITED_INDIRECT with a reason."
    )
```

- [ ] **Step 2: Run it; iterate.**

Run: `cd backend && python -m pytest tests/test_audit_coverage.py -q`
Expected at first: it prints any remaining uncovered routes. For each: either it's a real gap (add a record like Tasks 1-4) or a read-via-POST (add to `EXEMPT` with the exact path). Fix the exact `EXEMPT` paths by copying them from the failure output (paths must match `route.path` verbatim). Re-run until PASS.

- [ ] **Step 3: Full backend suite green**

Run: `cd backend && python -m pytest -q` then `ruff check app/`
Expected: all green.

- [ ] **Step 4: Commit**
```bash
git add backend/tests/test_audit_coverage.py
git commit -m "test(audit): guard that every mutating route audits or is allowlisted"
```

### Task 6: PR1 — open, green CI, squash-merge

- [ ] Push `feat/audit-viewer`, open PR "feat(audit): complete audit coverage + CI guard", ensure all required checks pass, squash-merge. Keep the branch for PR2 (or re-branch from updated main).

---

## PR2 — Audit read API (backend) — structured outline

Branch from updated `main`: `feat/audit-api`. **Requires a security-review pass** (global cross-tenant read gated only in code).

### Task 7: Relocate `AUDIT_VIEW` to org-level (superadmin-only)

- Modify `backend/app/core/rbac.py`: remove the line `Action.AUDIT_VIEW: {TENANT_ADMIN, OPERATOR, READ_ONLY},` from `_TENANT_MATRIX`; add `Action.AUDIT_VIEW,` to the `_ORG_ACTIONS` set. (The enum member already exists; `AUDIT_VIEW` is currently defined-but-unused.)
- Test (`backend/tests/test_rbac.py` or new): `can(is_superadmin=True, role=None, action=Action.AUDIT_VIEW) is True`; `can(is_superadmin=False, role="tenant_admin", action=Action.AUDIT_VIEW) is False`.
- TDD: write the two asserts first (the second fails today because it's tenant-granted), then relocate, then pass.

### Task 8: Schema + repository

- Create `backend/app/schemas/audit.py`:
  ```python
  class AuditEntryOut(BaseModel):
      id: uuid.UUID; ts: datetime
      actor_user_id: uuid.UUID | None; actor_email: str | None
      tenant_id: uuid.UUID | None; tenant_name: str | None
      action: str; target_type: str | None; target_id: str | None
      ip: str | None; details: dict
      model_config = ConfigDict(from_attributes=True)

  class AuditListOut(BaseModel):
      items: list[AuditEntryOut]; total: int
  ```
- Create `backend/app/repositories/audit.py` `AuditRepository(session)` with:
  - `async def query(self, *, actor_user_id, tenant_id, action, frm, to, limit, offset) -> tuple[list[Row], int]` — a `select(AuditLog, User.email, Tenant.name).outerjoin(User, AuditLog.actor_user_id == User.id).outerjoin(Tenant, AuditLog.tenant_id == Tenant.id)` with the optional `.where(...)` filters, `.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(limit).offset(offset)`; plus a `select(func.count())` over the same filters for `total`.
  - `async def stream(self, **same_filters_no_pagination) -> AsyncIterator[Row]` for CSV.
- Tests: insert a few `AuditLog` rows across two tenants + a NULL-actor row; assert filter-by-action, filter-by-tenant, date-range, and that enrichment yields email/tenant name (and None for the NULL cases).

### Task 9: Router + endpoints

- Create `backend/app/api/audit.py`:
  ```python
  router = APIRouter(prefix="/api/admin/audit", tags=["audit"])

  @router.get("", response_model=AuditListOut)
  async def list_audit(
      actor_user_id: uuid.UUID | None = None, tenant_id: uuid.UUID | None = None,
      action: str | None = None, frm: datetime | None = None, to: datetime | None = None,
      limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
      user: User = Depends(require_org(Action.AUDIT_VIEW)),
      session: AsyncSession = Depends(get_session),
  ) -> AuditListOut: ...

  @router.get("/export.csv")
  async def export_audit(... same filters ..., user=Depends(require_org(Action.AUDIT_VIEW)),
                         session=Depends(get_session)) -> StreamingResponse:
      # csv via a generator; Content-Disposition: attachment; filename="audit.csv"
  ```
  Build `AuditEntryOut` from each joined row (map `email`→`actor_email`, `name`→`tenant_name`).
- Mount in `backend/app/main.py`: `from app.api.audit import router as audit_router` + `app.include_router(audit_router)`.
- Tests (`backend/tests/test_audit_api.py`):
  - **authz:** non-superadmin (logged-in tenant_admin) → **403** on both `GET ""` and `GET "/export.csv"`; unauthenticated → 401.
  - **cross-tenant:** superadmin sees rows from both tenants (seed via `two_tenants` + audit rows).
  - **filters + pagination:** `action`, `tenant_id`, `frm/to`, `limit`/`offset`/`total`.
  - **csv:** header row + a data row present; `Content-Disposition` attachment.

### Task 10: Migration — supporting index

- Create `backend/migrations/versions/0037_audit_log_action_ts_index.py` (`revision="0037"`, `down_revision="0036"`):
  ```python
  def upgrade():
      op.create_index("ix_audit_log_action_ts", "audit_log", ["action", "ts"])
  def downgrade():
      op.drop_index("ix_audit_log_action_ts", table_name="audit_log")
  ```
- Verify `alembic upgrade head` applies cleanly on a fresh DB and the suite stays green.

### Task 11: Security-review + PR2

- Run the `security-reviewer` agent over the diff (focus: both endpoints gated, no secret in `details`, no SQL-injection via filters, `limit` capped). Address BLOCKER/IMPORTANT findings.
- Open PR "feat(audit): superadmin read API (list + CSV)", green CI, squash-merge.

---

## PR3 — Audit viewer (frontend) — structured outline

Branch from updated `main`: `feat/audit-ui`.

### Task 12: Regenerate the API client
- `cd frontend && npm run gen:api` (after PR2 is merged so the OpenAPI has the new routes). Commit the regenerated `src/api/schema.d.ts`.

### Task 13: i18n keys (English first, then mirror)
- `frontend/src/i18n/en.ts`: add `nav.audit`, an `audit` section (title, filters: actor/tenant/action/from/to, columns: time/actor/tenant/action/target/ip/details, `empty`, `export`), and `errors.auditLoad`.
- Mirror the exact keys in all 11 locales (`it es fr de pt nl ru ar zh zhTW ja`) — `tsc -b` enforces parity. Use parallel per-locale translation subagents (one file each → no conflicts), as done for prior milestones.

### Task 14: Data hook + page
- Create `frontend/src/audit/auditHooks.ts`: a `useAuditQuery(filters)` wrapping `api.GET("/api/admin/audit", { params: { query: filters } })` via react-query (key includes filters); and an `auditCsvUrl(filters)` helper building the `/api/admin/audit/export.csv?...` querystring.
- Create `frontend/src/pages/AuditPage.tsx`: filter controls (actor email, tenant select, action select/text, from/to date) + a Mantine `Table` (columns per the spec; details in an expandable cell) + offset/limit pagination bound to `total` + an **Export CSV** link/button (anchor to `auditCsvUrl`). Use `useT()` for all strings.

### Task 15: Route + superadmin-only nav
- Register the `/audit` route in the SPA router alongside the other authenticated pages.
- Add the nav entry **gated to superadmins only** — mirror how the existing superadmin-only entries (System / Log fleet) are conditionally rendered (find the nav component and the `is_superadmin`/role check it uses; reuse it).

### Task 16: Frontend tests + build gate
- `frontend/src/pages/__tests__/AuditPage.test.tsx`: renders rows from a mocked response; applying a filter refetches; pagination calls with new offset; the nav entry is hidden for a non-superadmin user. Stub `localStorage` if touched.
- Gate: `cd frontend && npm run build` (tsc -b + vite build) **must pass**; also `npm test` and `npm run lint`.

### Task 17: PR3
- Open PR "feat(audit): superadmin Audit viewer page", green CI, squash-merge.

---

## Release

### Task 18: Tag v0.10.0 + CHANGELOG
- Move the new entries from `[Unreleased]` into a `## [0.10.0] - <date>` section in `CHANGELOG.md` (Keep a Changelog) + add the compare link; small `docs(changelog): 0.10.0` PR, merge.
- Tag: `git tag -a v0.10.0 -m "v0.10.0"` then `git push origin v0.10.0`. The tag fires `publish-images.yml` (GHCR) and `release.yml` (GitHub Release body derived from the CHANGELOG section — see [[tag-version-on-feature-complete]]). Verify the Release shows the changelog.

---

## Self-review notes
- **Spec coverage:** PR1 = coverage gap-fill + guard (spec Part B). PR2 = read API + AUDIT_VIEW relocation + index (spec Part A backend + the `AUDIT_VIEW`-already-exists reconciliation). PR3 = viewer + CSV + i18n (spec Part A frontend). Release = v0.10.0.
- **Deviation from spec (intentional):** the spec said "add `Action.AUDIT_VIEW` org-level"; the enum member already exists as a *tenant-level, unused* action, so PR2 **relocates** it to org-level instead of adding a duplicate. Equivalent outcome, no new name.
- **Off-by-ones resolved:** `templates/preview` and `profiles/preview` are reads-via-POST → EXEMPT (not gaps). Only `mfa/setup` needs a runtime classification (Task 4). Real gaps to fill: firmware/action, setup, send-now.
- **Type consistency:** `AuditEntryOut`/`AuditListOut` used identically across repository → router → tests; `Action.AUDIT_VIEW` is the single gate symbol; `ix_audit_log_action_ts` is the single index name.
```

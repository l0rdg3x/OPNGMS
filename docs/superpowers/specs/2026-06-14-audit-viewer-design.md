# Audit Viewer (superadmin) + complete audit coverage — Design

**Date:** 2026-06-14
**Status:** Approved (design); ready for implementation plan.
**Branch:** `feat/audit-viewer`

## Problem

OPNGMS already records a thorough **application audit ledger** — the `audit_log` table
(`app/models/audit.py`), written via `AuditService.record(...)` from **53 call sites** across the API and
services. It captures, for each event: `ts, actor_user_id, tenant_id, action, target_type, target_id, ip,
details (JSONB)`. Coverage spans auth, devices, config changes, RBAC (groups/memberships), MFA, log
forwarding, profiles, templates, reports/schedules, SMTP and system settings, plus the CLI break-glass reset.

But there is **no read path**:

- No `GET` endpoint returns audit rows (no `/audit` route anywhere).
- No frontend page (no `AuditPage.tsx`; the only frontend reference is a comment in the generated schema).
- The ledger is therefore **write-only**, readable today only by querying Postgres directly.

Separately, the write coverage — while broad — is **not complete and not regression-proof**. A route-by-route
audit found unaudited **mutating** routes, most notably `POST .../firmware/action` (schedules firmware
upgrade / reboot / plugin install-remove on the box — one of the highest-impact actions in the product).
Nothing prevents a future mutating route from shipping without an audit record.

This milestone closes both gaps: a **superadmin-only Audit viewer** (read API + UI) and **complete,
regression-proof audit coverage**.

## Goals

1. Superadmins can browse the full cross-tenant audit ledger from the UI, with filters and pagination, and
   export the filtered view to CSV.
2. Every state-changing/privileged action on OPNGMS is recorded in `audit_log`, and a CI guard makes it
   **stay** that way (a new mutating route without audit fails the build).

## Non-goals

- **No tenant-admin / per-tenant audit view.** Superadmin-only, global. (A tenant slice can be added later.)
- **No retention / deletion.** The audit ledger is append-only and kept indefinitely (compliance-friendly).
  No sweep job, no hypertable conversion.
- **No change to what `details` contains** beyond the gap-fill additions. We only verify it carries no secrets.
- **No keyset pagination** in v1 (offset/limit is enough; date-range filter is the primary scoping). Noted as
  a future optimization if the table grows very large.

## Decisions (user-approved 2026-06-14)

| Decision | Choice |
|----------|--------|
| Access | **Superadmin only**, global cross-tenant view |
| Retention | **Keep everything** (ledger; no automatic deletion) |
| Coverage | **Gap-fill now + a CI guard test** that forces every mutating route to audit |
| Export | **Viewer + CSV export** |

## Architecture

Two complementary halves. **No change to the `audit_log` model itself** — it already carries every field
the viewer needs. We add the read layer (API + UI) and complete the write layer (gap-fill + guard).

```
[ mutating routes ] --AuditService.record()--> [ audit_log table ] <--SELECT-- [ GET /api/admin/audit ]
        ^                                                                                  |
   guard test enforces                                                          [ AuditPage.tsx (/audit) ]
   every mutating route                                                           superadmin-only nav
```

### Part B — Complete audit coverage (build first; ships value alone)

**B1. Gap-fill the unaudited mutating routes.** From the route-by-route scan, the mutating routes that do
**not** record audit today, and the action string each should record:

| Route | New `action` | Notes |
|-------|--------------|-------|
| `POST /tenants/{t}/devices/{d}/firmware/action` | `device.firmware.action` | **High impact.** actor = `ctx.user.id`, `target_type="device"`, `target_id=device_id`, `details={kind, target, scheduled_at}`. Record at request time (who *scheduled* it), independent of any worker-side execution record. |
| `POST /setup` | `setup.bootstrap` | First-superadmin bootstrap. actor = the new user's id, `tenant_id=None`, `details` minimal (email). One-time, but privileged. |
| `POST /report-schedules/{id}/send-now` | `report.schedule.send_now` | Immediate manual send trigger. actor + tenant + `target_id=schedule_id`. |

**Off-by-one routes to classify during the plan** (mutating count > record count by one): the MFA setup-start
(`POST /me/mfa/setup`), the 5th `profiles.py` route, and the 6th `templates.py` route. For each, the plan
decides: if it changes state → add an audit record; if it is a read/preview performed via POST → add it to
the **EXEMPT allowlist** (below) with a one-line justification.

**Known-exempt (reads performed via POST because they carry a body) — must be on the allowlist:**
`POST .../firmware/check` (queries the box for updates), `POST /logs/search` (log search).

**Secrets invariant.** Confirm the `details` payloads recorded (existing + new) never include secrets
(device api key/secret, SMTP password, CA key, MFA secret). The viewer returns `details`, so this upholds the
"secrets never returned/logged" invariant. The firmware-action `details` (kind/target/scheduled_at) is safe.

**B2. CI guard test** — `backend/tests/test_audit_coverage.py`:

- Introspect the FastAPI app (`app.main:app`) routes; collect every route whose methods include any of
  `POST/PUT/PATCH/DELETE`.
- For each such route, assert one of:
  - its handler **records audit** — detected by scanning the handler's source (`inspect.getsource`) for an
    `AuditService(...).record(` / `.record(` call; **or**
  - it is on the explicit **`EXEMPT`** set (route path+method → reason), for genuine reads-via-POST; **or**
  - it is on an explicit **`AUDITED_INDIRECT`** set, for the rare route that audits inside a service it calls
    (path+method → where). Keeping this list explicit means an indirection is a deliberate, reviewed choice.
- The test fails listing any mutating route that is neither audited nor allowlisted. This is the durable
  "ensure everything is audited" mechanism — same spirit as the existing `rekey_secrets` metadata guard test.
- **Limitation (documented in the test):** the source scan detects inline audit calls (the current pattern in
  `api/*.py`); routes that delegate auditing to a service must be added to `AUDITED_INDIRECT`. This is
  intentional — it forces a human decision rather than silently passing.

### Part A — Read API (backend)

New router `app/api/audit.py`, mounted under the admin surface, **superadmin-gated**.

- **Authz:** add an org-level action `Action.AUDIT_VIEW` to `app/core/rbac.py` (org-level actions are allowed
  only to the superadmin) and gate both endpoints with `Depends(require_org(Action.AUDIT_VIEW))` — the same
  pattern `system.py` uses with `require_org(Action.SYSTEM_MANAGE)`. A non-superadmin receives **403**.
- **Why code-gating matters:** `audit_log` is **not** under RLS (not in `app/core/rls.py TENANT_TABLES`;
  `tenant_id` is nullable). The API role `opngms_app` can `SELECT` all rows (blanket grant). So nothing in the
  database scopes this query — the superadmin gate in code is the *only* control. The plan must include an
  explicit authz test (non-superadmin → 403) and a test that the result includes rows from multiple tenants.

**`GET /api/admin/audit`** — list, newest first.

- Query params (all optional): `actor_user_id: UUID`, `tenant_id: UUID`, `action: str` (exact match),
  `frm: datetime`, `to: datetime`, `limit: int` (default 50, max 200), `offset: int` (default 0).
- Ordered by `ts DESC, id DESC`. Returns `{ items: [...], total: int }` so the UI can paginate.
- **Enrichment** (joins; both target tables are non-RLS / global):
  - `actor_user_id → actor_email` via `users` (LEFT JOIN — actor may be NULL for system/break-glass rows;
    render as "system").
  - `tenant_id → tenant_name` via `tenants` (LEFT JOIN — tenant_id may be NULL for org-level actions).
- Response item shape (new schema `AuditEntryOut`):
  `{ id, ts, actor_user_id, actor_email, tenant_id, tenant_name, action, target_type, target_id, ip, details }`.

**`GET /api/admin/audit/export.csv`** — same filters (no pagination; streams all matching rows, newest
first), `text/csv` with a `Content-Disposition: attachment` header. Columns mirror the list item (with
`details` JSON-encoded into one cell). Superadmin-gated identically.

**Migration** (`backend/migrations/versions/`, forward-only): add a composite index
`ix_audit_log_action_ts (action, ts DESC)` to support the common "filter by action over a date range" query.
Single-column indexes on `ts`, `actor_user_id`, `tenant_id` already exist and cover the other filters.

### Part A — Read UI (frontend)

- **Page** `frontend/src/pages/AuditPage.tsx`, route `/audit`. Nav entry **visible only to superadmins**
  (mirror how other superadmin-only nav entries are gated).
- **Filters:** actor (text → email contains, resolved client-side or via param), tenant (select of tenants),
  action (select populated from a known action list, or free text), date range (from / to). Changing a filter
  refetches via react-query (keys include the filter values).
- **Table columns:** timestamp (localized), actor (email or "system"), tenant (name or "—"), action,
  target (`target_type` + `target_id`), IP, **details** (expandable JSON cell). Offset/limit pagination
  controls bound to `total`.
- **Export CSV** button → calls `/api/admin/audit/export.csv` with the current filters; browser downloads.
- **i18n:** new keys across all 12 locales — `nav.audit`, the page title, column headers, filter labels, the
  empty state, and an `errors.auditLoad` key. Add English first (`en.ts`), then mirror in
  `it es fr de pt nl ru ar zh zhTW ja` (compiler-enforced parity via `tsc -b`).
- Regenerate the typed API client (`npm run gen:api`) after PR2 lands so the new endpoints are typed.

## Data model

No new table; no column change. `audit_log` already has `id, ts, actor_user_id, tenant_id, action,
target_type, target_id, ip, details`. One additive index migration (`ix_audit_log_action_ts`). The table
stays a regular table (not a hypertable) — we keep everything, so no time-based retention/partitioning is
needed.

## Security considerations

- **Single control is the code gate.** Because `audit_log` is not RLS-scoped, the superadmin check in the
  endpoint is the only thing preventing cross-tenant disclosure. Both endpoints (list + CSV) must be gated;
  the CSV endpoint is easy to forget — explicitly test it. → **PR2 requires a security-review pass.**
- **No secret leakage via `details`.** The viewer surfaces `details`. Verify (and keep verifying, in the
  guard test's spirit) that recorded `details` never carry secrets.
- **CSRF / methods.** Read endpoints are `GET`, so no CSRF token needed (CSRF guard is on mutating routes).
- **Input safety.** `action` is matched exactly (no LIKE injection surface); dates are parsed to `datetime`;
  `limit` is capped (max 200) to bound response size; the CSV stream is bounded by the filters (document that
  an unfiltered export can be large — acceptable for a superadmin tool).

## Testing

- **B (coverage):** unit tests asserting each gap-filled route writes the expected `audit_log` row
  (action/target/actor/ip). The **guard test** itself (every mutating route audited-or-allowlisted).
- **A (API):** authz (non-superadmin → 403 on both list and CSV); filter correctness (by actor, tenant,
  action, date range); pagination (`limit`/`offset`/`total`); enrichment (actor/tenant names; NULL actor →
  system, NULL tenant → none); CSV shape (headers + a sample row).
- **A (frontend):** AuditPage renders rows, applies a filter (refetch), paginates, triggers CSV download;
  nav entry hidden for non-superadmin. Build gate: `npm run build` (tsc -b + vite build).

## Decomposition (3 PRs + release)

1. **PR1 — Audit coverage (backend).** Gap-fill the unaudited mutating routes (firmware/action, setup,
   send-now, + classify the 3 off-by-ones), add the `EXEMPT`/`AUDITED_INDIRECT` allowlists, add
   `test_audit_coverage.py` and per-route record tests. Self-contained; ships "everything is audited now".
2. **PR2 — Audit read API (backend).** `Action.AUDIT_VIEW`, `app/api/audit.py` with list + `export.csv`,
   `AuditEntryOut` schema, actor/tenant enrichment, filters, offset pagination, the `ix_audit_log_action_ts`
   migration, and authz/filter/CSV tests. **Security-review required.**
3. **PR3 — Audit viewer (frontend).** `AuditPage.tsx` + `/audit` route + superadmin-only nav + filters/table/
   pagination + CSV export + i18n (12 locales) + `gen:api` + tests.
4. **Release.** Tag **v0.10.0** + CHANGELOG entry (Keep a Changelog), which the release workflow turns into
   the GitHub Release body.

## Risks / open items

- **Guard-test detection is heuristic** (source scan + allowlists). Accepted: it errs toward failing (forcing
  a human to either add audit or justify an allowlist entry), which is the safe direction.
- **`firmware/action` may already be recorded on execution by the worker.** The plan verifies; regardless we
  add the **request-time** record (captures the requesting user + IP, which the worker context lacks).
- **Deep offset pagination** could be slow on a very large ledger; mitigated by the date-range filter and the
  new `(action, ts)` index. Keyset pagination is the documented future upgrade.
```

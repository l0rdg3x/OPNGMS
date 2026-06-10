# OPNGMS — Phase 5 / Milestone 5E: Scheduled Reports + Storage/History + UI — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user chose on-demand + periodic cron, and to proceed through 5B–5E)
- **Phase:** 5 of 5 — Milestone 5E (the final reporting milestone)
- **Depends on:** 5A–5D (engine + sections + white-label), ARQ worker, RLS, RBAC — in `main`
- **Enables:** Phase 5 complete (reporting end-to-end)

## 1. Context

5A–5D generate a per-tenant white-label report **on demand** (returned inline). 5E adds: **persistence**
(every generated report is stored), a **history list + download**, an **on-demand UI** (generate +
download from the browser), and a **periodic ARQ cron** that auto-generates a monthly report per tenant.

## 2. Design decisions (5E)

| Topic | Decision |
|-------|----------|
| Storage | A `generated_reports` table (tenant-scoped, RLS) holding the PDF **bytea** + metadata (kind, period, size, created_by). DB storage is fine at this scale (object store later — debt). |
| What's stored | Both **on-demand** (POST stores + returns inline) and **scheduled** (cron) reports → one history. |
| Scheduling | An ARQ **monthly cron** (day 1, ~04:00) enumerates active tenants and enqueues a per-tenant generate job for the **prior calendar month**. Worker runs as owner (RLS bypassed); the aggregator's explicit `tenant_id` filters keep each report correctly scoped (same trust model as the poller). |
| Worker job | `generate_tenant_report(tenant_id, frm, to, kind)` builds via `ReportService` and inserts a `generated_reports` row. |
| API | `POST /reports` now also **stores** the result (kind `on_demand`, `created_by`) + returns the PDF inline. `GET /reports` lists history metadata (newest first). `GET /reports/{id}/download` returns the PDF bytes. |
| RBAC | Generate/store needs `REPORT_GENERATE` (existing); list + download need `DEVICE_VIEW` (any member can read the tenant's reports). |
| Frontend | A **"Reports" page** (`/reports`): a generate form (date range) → POST → browser download; a history table (period/kind/created/size) with per-row **Download**. The generate form is shown only for `REPORT_GENERATE` roles; the history is visible to all members. |

## 3. Data model — `generated_reports` (tenant-scoped, RLS)

```
generated_reports(
  id          UUID PK,
  tenant_id   UUID NOT NULL,                 -- RLS
  kind        TEXT NOT NULL,                 -- 'on_demand' | 'scheduled'
  period_from TIMESTAMPTZ NOT NULL,
  period_to   TIMESTAMPTZ NOT NULL,
  created_by  UUID,                          -- the user (on-demand); NULL for scheduled
  pdf         BYTEA NOT NULL,                -- the report bytes
  size        INTEGER NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
```
- RLS (ENABLE+FORCE + `tenant_isolation` + app-role grant; added to `TENANT_TABLES`); index `(tenant_id, created_at DESC)`. Migration `0012`.
- The list API returns metadata only (NOT the bytes); bytes only via the download endpoint.

## 4. Worker

- `generate_tenant_report(ctx, tenant_id, frm, to, kind)`: open an owner session; load the `Tenant` (name);
  `pdf = await ReportService(session, tenant_id).build_report(tenant_name=tenant.name, frm=frm, to=to)`;
  insert a `generated_reports` row (`kind`, period, `pdf`, `size=len(pdf)`, `created_by=None`); commit.
- `enqueue_scheduled_reports(ctx)` cron: enumerate active tenants (`select(Tenant.id).where(status='active')`);
  compute the **prior calendar month** `[first_of_prev_month, first_of_this_month)`; enqueue
  `generate_tenant_report(str(tid), frm_iso, to_iso, "scheduled")` for each. Registered in
  `WorkerSettings.functions` + a `cron(enqueue_scheduled_reports, day={1}, hour={4}, minute={0})`.
- Datetimes passed as ISO strings (ARQ-serializable); the job parses them.

## 5. API (in `app/api/reports.py`)

- **`POST /reports`** (existing, `REPORT_GENERATE`): build → **store** a `generated_reports` row (kind
  `on_demand`, `created_by=ctx.user.id`) → return the PDF inline (as today) + audit.
- **`GET /reports`** (`DEVICE_VIEW`): list `GeneratedReportOut` (id, kind, period_from/to, size,
  created_at, created_by) newest-first, tenant-scoped.
- **`GET /reports/{report_id}/download`** (`DEVICE_VIEW`): fetch the row (tenant-scoped → 404 if missing/
  cross-tenant) → `Response(pdf, media_type="application/pdf", Content-Disposition attachment)`.
- All tenant-scoped under RLS; cross-tenant → 404.

## 6. Frontend — Reports page (`/reports`)

- Hooks: `useGeneratedReports()` (GET list, tenant-scoped), `useGenerateReport()` (POST → returns a PDF
  blob → trigger a browser download + invalidate the list), `downloadReport(id)` (GET
  `/reports/{id}/download` as a blob → browser download). For blob responses use `api.*` with
  `parseAs: "blob"` or a direct `fetch` with `credentials: "include"`.
- Page: a generate form (`from`/`to` `DateTimePicker`) + Generate button (shown for `REPORT_GENERATE`
  roles), and a history `Table` (Period / Kind / Created / Size / Download). Nav link "Reports".
- i18n; tests (list renders; generate calls POST + downloads; download calls the endpoint; read-only
  hides the generate form but shows history).

## 7. Security & safety

- **RLS** on `generated_reports`; list/download tenant-scoped (a cross-tenant download → 404). Worker
  generates as owner but the aggregator's explicit `tenant_id` filters scope each report (no cross-tenant
  bleed — same model as metrics/event writes).
- **RBAC**: generate `REPORT_GENERATE`; list/download `DEVICE_VIEW`. CSRF + audit on POST.
- **No secrets** in reports (events/metrics only; config secrets never read). PDF bytes returned only via
  the authenticated, tenant-scoped download endpoint.
- **DoS bound**: report size is bounded by the existing range cap + the report content; history grows over
  time (a retention policy is later debt).

## 8. Milestone 5E breakdown (for the plan)
1. **Model + migration + RLS + repository** (`generated_reports`) + tests.
2. **Worker**: `generate_tenant_report` job + `enqueue_scheduled_reports` cron (prior month) + registration; tests (job stores a row; cron enumerates active tenants + prior-month range).
3. **API**: POST stores; `GET /reports` (list) + `GET /reports/{id}/download`; RBAC/CSRF/RLS + cross-tenant 404 tests.
4. **Frontend**: Reports page (generate + history + download) + hooks + nav + i18n + tests.
5. **Technical debt**.

## 9. Definition of "Done" (5E)
- Generating a report (on-demand or by the monthly cron) **stores** it; a member can see the tenant's
  report **history** and **download** any report; the on-demand UI generates + downloads from the browser.
- Tenant-scoped + RLS-isolated (cross-tenant download → 404); generate gated by `REPORT_GENERATE` + CSRF +
  audit; list/download `DEVICE_VIEW`. Backend + frontend suites green; migration applies cleanly.

## 10. Non-goals (5E) / deferred
- **Object-store storage** + **retention/pruning** of old reports (DB bytea for now).
- **Email/delivery** of scheduled reports (generation + storage only; delivery is a later phase).
- **Configurable schedules per tenant** (fixed monthly cron for now).

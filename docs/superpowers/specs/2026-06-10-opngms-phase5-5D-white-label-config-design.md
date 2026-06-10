# OPNGMS — Phase 5 / Milestone 5D: Per-Tenant White-Label Report Config — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user chose "full white-label" and to proceed through 5B–5E)
- **Phase:** 5 of 5 — Milestone 5D (per-tenant report branding + a settings UI)
- **Depends on:** 5A (engine: `ReportService`/`build_context` with branding placeholders), RLS, RBAC, frontend shell — in `main`
- **Enables:** 5E (scheduled reports + history)

## 1. Context

5A–5C render reports with **branding placeholders** (tenant name, `owner=None`, default title/timezone).
5D makes the branding **per-tenant white-label**: a tenant admin configures the **report title**,
**owner**, **timezone**, and uploads a **logo**, persisted and applied to every generated report (the
title page shows the logo + title; the footer shows owner + timezone). A **settings UI** drives it.

## 2. Design decisions (5D)

| Topic | Decision |
|-------|----------|
| Storage | A `report_settings` table, **one row per tenant** (`tenant_id` PK), RLS-protected; logo stored as **bytea + mime** in the DB (no object store — keeps it tenant-scoped + simple). |
| Logo format | **PNG/JPEG only** (validated by magic bytes + size cap ~512 KB). **SVG is rejected** (script/XXE risk). Embedded into the PDF as a **`data:` URI** (inline — no network). |
| SSRF (logo) | The report's `url_fetcher` is updated to **allow only the `data:` scheme** (delegating to WeasyPrint's default inline decoder) and **block all other schemes** (http/https/file/ftp). `data:` is inline → no SSRF. |
| RBAC | New action **`REPORT_CONFIG`** (admin config) granted to **`tenant_admin`** only. Reading settings (metadata) needs `DEVICE_VIEW`; writing settings + logo needs `REPORT_CONFIG`. CSRF + audit on writes. |
| API shape | `GET /reports/settings` → metadata (title/owner/timezone + `has_logo`, NOT the bytes). `PUT /reports/settings` → set fields. `PUT /reports/settings/logo` (multipart) → upload; `DELETE /reports/settings/logo` → clear. |
| Engine wiring | `ReportService.build_report` loads the tenant's settings; `build_context` uses `title`/`owner`/`timezone` from settings (falling back to defaults), and the title page embeds the logo `data:` URI when present. |
| Frontend | A **"Reports" settings page** (tenant_admin only; hidden for others) with the form + logo upload/preview, behind a nav link. |

## 3. Data model — `report_settings` (tenant-scoped, RLS)

```
report_settings(
  tenant_id   UUID PK,                         -- one row per tenant (RLS keyed on this)
  title       TEXT NOT NULL DEFAULT 'Security & Activity Report',
  owner       TEXT NOT NULL DEFAULT '',
  timezone    TEXT NOT NULL DEFAULT 'UTC',
  logo        BYTEA,                            -- nullable; PNG/JPEG bytes
  logo_mime   TEXT,                             -- 'image/png' | 'image/jpeg'
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
```
- Added to `TENANT_TABLES` (`app/core/rls.py`) → ENABLE+FORCE RLS + `tenant_isolation` policy + app-role grant in a migration (`0011`). One row per tenant (upsert on first write).
- The logo bytes are **never returned by the JSON API** (only `has_logo`); they are embedded server-side at generation time. (Optional: a separate authenticated logo-preview endpoint returns the image for the settings UI — gated by `DEVICE_VIEW`, tenant-scoped.)

## 4. API (under the tenant prefix)

- `GET /api/tenants/{tenant_id}/reports/settings` → `ReportSettingsOut { title, owner, timezone, has_logo, logo_mime }` (`DEVICE_VIEW`).
- `PUT /api/tenants/{tenant_id}/reports/settings` (body `{title, owner, timezone}`) → upsert (`REPORT_CONFIG` + CSRF + audit `report.settings.update`).
- `PUT /api/tenants/{tenant_id}/reports/settings/logo` (multipart `file`) → validate (magic bytes PNG/JPEG, ≤512 KB), store (`REPORT_CONFIG` + CSRF + audit `report.settings.logo`). Reject others → 400.
- `DELETE /api/tenants/{tenant_id}/reports/settings/logo` → clear (`REPORT_CONFIG` + CSRF + audit).
- (Optional) `GET /api/tenants/{tenant_id}/reports/settings/logo` → the raw image (`DEVICE_VIEW`) for the UI preview.
- All tenant-scoped under RLS; cross-tenant access → 404/empty.

## 5. Engine wiring

- `ReportSettingsRepository(session, tenant_id)`: `get()` (returns the row or defaults), `upsert(...)`, `set_logo(bytes, mime)`, `clear_logo()`.
- `ReportService.build_report`: load settings → pass `title`, `owner`, `timezone`, and a `logo_data_uri` (built from logo bytes+mime, or `None`) into `build_context`. `ReportContext` gains a `logo_data_uri: str | None`; the template title page renders `<img src="{{ logo }}">` when present (the `data:`-allowing fetcher makes this safe).
- The endpoint's caller no longer passes a hardcoded `timezone`; settings provide it (the request may still override the range only).

## 6. Security & safety

- **Logo validation:** accept only PNG (`\x89PNG`) / JPEG (`\xFF\xD8\xFF`) by **magic bytes** (not just content-type), size ≤ 512 KB; reject SVG and everything else → 400. Stored as bytea; embedded as a `data:` URI (inline).
- **SSRF:** the report `url_fetcher` allows ONLY `data:` (inline) and blocks all network/file schemes — verified that `data:` decodes inline and `http(s)` is dropped.
- **RBAC/CSRF/audit:** writes require `REPORT_CONFIG` (tenant_admin) + CSRF + audit; reads require `DEVICE_VIEW`. Tenant-scoped under RLS (a cross-tenant settings write/read is impossible).
- **No secrets:** settings carry no credentials; the logo is not sensitive. Autoescape stays ON for title/owner (untrusted-ish admin input rendered as escaped text; never a URL attribute).
- **DoS bound:** logo size cap; the JSON API never returns the bytes (small responses).

## 7. Milestone 5D breakdown (for the plan)
1. **Model + migration + RLS + RBAC**: `ReportSettings` model, migration `0011` (table + RLS + grant), add to `TENANT_TABLES`, `REPORT_CONFIG` action; model/migration tests.
2. **Repository + engine wiring**: `ReportSettingsRepository`; `build_context`/`ReportContext` gain branding + `logo_data_uri`; template title page renders the logo; the `url_fetcher` allows `data:`; service loads settings; tests (settings applied, logo embedded, fetcher allows data/blocks http).
3. **API**: GET/PUT settings + logo upload/delete (+ optional logo preview), RBAC/CSRF/audit, magic-byte validation; tests incl. cross-tenant isolation + bad-logo rejection.
4. **Frontend**: settings hooks + a tenant_admin-only "Reports" settings page (form + logo upload/preview) + nav link + i18n; Vitest/MSW tests.

## 8. Definition of "Done" (5D)
- A tenant admin can set the report title/owner/timezone and upload a PNG/JPEG logo; generated reports
  show the logo on the title page and the owner/timezone in the footer (from settings, not placeholders).
- Logo validated by magic bytes + size; embedded as an inline `data:` URI; the report fetcher allows only
  `data:` (no SSRF). Writes gated by `REPORT_CONFIG` + CSRF + audited; reads `DEVICE_VIEW`; tenant-scoped + RLS.
- Backend + frontend suites green; `alembic` migration clean.

## 9. Non-goals (5D) / deferred
- **Scheduled reports + storage/history + on-demand UI button** (5E).
- **Per-report-template themes / color palettes** beyond logo+title+owner (later nicety).
- **SVG logos** (rejected for safety).

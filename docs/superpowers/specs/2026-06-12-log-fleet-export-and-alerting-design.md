# C13 — Log-fleet export (CSV+PDF) + silent-tenant alerting (email+dashboard)

Two focused PRs.

## PR-A — Fleet export (CSV + PDF)

**Goal:** let the superadmin download the MSP log-fleet table as CSV or PDF (honouring the window).

- **`GET /api/admin/log-fleet/export?window=24h|7d|30d&format=csv|pdf`** (`LOG_FLEET_VIEW`). Reuses
  `log_fleet_overview(session, settings, window_hours)`; returns a buffered `Response` with a
  `Content-Disposition: attachment` header. `format` defaults to `csv`; an invalid format → 400.
- **`app/services/log_fleet_export.py`** (pure, testable):
  - `fleet_rows_to_csv(rows, *, now, stale_after) -> str` — header + one row per tenant
    (`tenant_name, enabled, disabled, revoked, total_devices, last_log_at, volume, silent`).
  - `fleet_rows_to_html(rows, *, window, generated_at, now, stale_after) -> str` — a small escaped
    HTML table for `html_to_pdf()` (reuses the reporting WeasyPrint renderer).
  - `_silent(row, now, stale_after)` — same rule as the overview: `enabled>0 and (last_log is None
    or now-last_log > stale_after)`. Uses `log_fleet.STALE_AFTER` (1h) for parity with the UI badge.
- **Frontend:** "Export CSV" / "Export PDF" buttons on `LogFleetPage` → `downloadLogFleet(window,
  format)` uses `api.GET(..., { parseAs: "blob" })` and triggers a download. The window follows the
  current selector.

## PR-B — Silent-tenant alerting (detection + email + dashboard)

**Goal:** proactively alert the MSP when a tenant goes silent (enabled forwarding, no recent logs).

- **Settings** (`core/config.py`): `silent_alert_enabled=True`, `silent_alert_after_hours=6`
  (alert threshold — higher than the UI's 1h badge), `silent_alert_cron_minute=0` (hourly).
- **Model + migration** `silent_tenant_alerts` (global, non-RLS, one row per silent tenant):
  `tenant_id` (unique, FK→tenants ondelete CASCADE), `tenant_name`, `silent_since`, `last_alert_at`,
  `details` JSONB. Grants via `grant_app_role_statements()`.
- **Worker cron `detect_silent_tenants(ctx)`** (hourly, owner session): compute per-tenant silence
  (`fleet_forwarding_counts` + `fleet_log_stats`) at the **alert** threshold. For each tenant:
  - alert-silent AND no row → **create** the row + **send one email** to the active superadmins
    (enter episode). 
  - not alert-silent AND row exists → **delete** the row (recovery).
  - alert-silent AND row exists → no-op (dedup: one email per episode).
- **Email**: generalize `email/smtp.py` so an attachment is optional (`send_email(..., attachment=
  None)`); `send_report_email` becomes a thin wrapper. The alert email lists the newly-silent
  tenants. Skips cleanly when SMTP is disabled/unconfigured.
- **API**: `GET /api/admin/silent-tenant-alerts` (`LOG_FLEET_VIEW`) → the active alert rows.
- **Frontend**: a banner on `LogFleetPage` listing the tenants currently in alert (from the new
  hook), shown only when non-empty.

## Testing
- PR-A: pure CSV/HTML renderers (silent column, escaping, empty); endpoint returns the right
  media-type + Content-Disposition for csv/pdf, invalid format → 400, non-superadmin → 403.
- PR-B: detection state machine (enter→create+email, persist→no re-email, recover→delete) with a
  fake SMTP + owner session; email built to the right recipients; endpoint + RBAC; frontend banner.

## Out of scope
- Per-tenant alert routing / escalation policies; recovery ("resolved") emails (only entry emails).

# Report Delivery & Scheduling — Design Spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** #6 in the 2026-06-11 ordered TODO batch.

## Goal

Turn the existing on-demand PDF reports into a **scheduled email-delivery** system. The superadmin
configures one authenticated SMTP relay; each tenant (and optionally each device) gets its own
delivery schedule, cadence, and recipient list. Reports are generated on a cron, stored as today, and
emailed as a PDF attachment.

## Background (what exists today)

- `ReportService(session, tenant_id).build_report(*, tenant_name, frm, to, locale=None) -> bytes`
  renders a tenant PDF. `build_context()` iterates over **every** device of the tenant
  (`aggregator.devices()`), producing one section per device. The aggregator already accepts a
  `device_id` filter on every query — only `build_context` lacks a single-device path.
- `GeneratedReport` (tenant-scoped, RLS) stores past PDFs. It has `kind ∈ {on_demand, scheduled}` but
  **no `device_id`**.
- `ReportSettings` (tenant-scoped) already has `language`, `title`, `owner`, `timezone`, `logo`.
- The worker runs a **fixed weekly cron** (`enqueue_scheduled_reports` → `generate_tenant_report`),
  generating + storing (no email) the prior calendar week for every active tenant.
- **No email/SMTP code exists anywhere.** `aiosmtplib` is not yet a dependency (`email-validator`,
  `argon2-cffi`, `pyotp` are).
- Global non-tenant singletons use `app_settings` (JSONB KV, e.g. the MFA policy). Encrypted secrets
  (device API secrets) use a `LargeBinary` column + Fernet via `app.core.crypto`.
- Superadmin-only endpoints gate on `require_org(Action.USER_MANAGE)`; tenant report config gates on
  `require_tenant(Action.REPORT_CONFIG)` (tenant_admin). Writes require `enforce_csrf`.

## Locked decisions (from brainstorming)

1. **SMTP is global** (one relay, configured by the superadmin: host/port/security/username/password)
   **+ per-tenant sender override** (`report_settings.from_email`, for white-labelling). NOT per-tenant
   SMTP servers.
2. **Two report scopes**, each with its own schedule + recipients:
   - **Tenant report** — the whole fleet (all of the tenant's devices), as today.
   - **Per-device report** — a single device/site (for a different site contact).
3. **Recipients are multiple addresses**, set per schedule (so tenant-level and device-level recipient
   lists are independent).
4. **Frequency is a preset** at a configured UTC hour — replacing the fixed weekly cron:
   - **Weekly** — every 7 days on a chosen weekday (Mon..Sun).
   - **Monthly** — the first day of each calendar month.
   - **On demand** — no automatic cadence; delivered only via a manual "send now" trigger (which also
     works for weekly/monthly schedules).
5. **Delivery retries** on SMTP failure: re-attempt **every 10 minutes for up to 2 hours** (12 retries)
   before giving up. The report PDF is generated/stored once; only the send is retried.
6. **Future TODO (record, do not build):** OAuth-based sending (Gmail / M365) instead of an SMTP
   password.

## Architecture

```
                       ┌──────────────────────────────────────────────┐
   superadmin ───PUT──▶│ smtp_settings (global singleton, pwd enc'd)  │
                       └──────────────────────────────────────────────┘
                                          │ read+decrypt
   tenant_admin ──PUT──▶ report_schedule  │                 ┌─────────────────┐
     (tenant &           (tenant_id,      ▼                 │ EmailService    │
      device rows,        device_id?,  ┌──────────────┐     │ (aiosmtplib)    │
      frequency,          recipients[]) │ worker cron  │     │ send PDF+body   │──▶ recipients
      weekday/hour)                     │ hourly:      │     └─────────────────┘
       │ POST send-now                  │ enqueue_due_ │              ▲ retry ≤2h (every 10m)
       ▼                                │ reports      │              │
  deliver_scheduled_report(schedule_id, manual?): ─────▶ build (tenant|device) → store
        build + store + advance next_run_at            GeneratedReport → send_report_email_job(report_id)
```

### Component boundaries

- **`smtp_settings` (global singleton table + service)** — one row; the SMTP relay config. Password
  encrypted at rest (Fernet). Read only by the worker (to send) and the superadmin API (write/test;
  never returns the password). Owns: SMTP transport config + the default `from_email`.
- **`report_schedule` (tenant-scoped table + repo)** — the per-tenant and per-device schedules.
  `device_id IS NULL` ⇒ tenant/fleet scope; `device_id` set ⇒ that device. Owns: enabled flag,
  cadence (`every_n_days`), `hour`, `recipients[]`, `next_run_at`/`last_run_at`.
- **`EmailService` (`app/services/email/smtp.py`)** — pure transport: given a resolved send config and
  a message (subject, from, recipients, body, one attachment), deliver it via aiosmtplib. Knows
  nothing about reports or tenants. Raises `EmailSendError` on failure.
- **`ReportService` / `build_context` (extended)** — gains an optional `device_id` so it can render a
  single-device report. Tenant report = `device_id=None` (unchanged behaviour).
- **Worker delivery (`enqueue_due_reports` cron + `deliver_scheduled_report` job)** — replaces the
  weekly cron. The hourly cron finds due schedules and enqueues one isolated job each; the job builds,
  stores, emails, and advances the schedule.
- **API + frontend** — superadmin SMTP page (config + test send); tenant report-config page gains a
  schedule editor (tenant schedule + per-device schedules), a recipients editor, the sender override,
  and the (already-backed) language picker.

## Data model

### New table: `smtp_settings` (global, non-tenant, single row)

Migration `0022`. Single-row guard via a fixed smallint PK with a CHECK.

| column        | type            | notes                                                       |
|---------------|-----------------|-------------------------------------------------------------|
| `id`          | SmallInteger PK | CHECK (`id = 1`) — enforces a single row                    |
| `enabled`     | Boolean         | master switch; default `false` (delivery off until set up)  |
| `host`        | String          | SMTP server hostname                                        |
| `port`        | Integer         | default `587`                                               |
| `security`    | String          | `starttls` \| `tls` \| `none` (default `starttls`)          |
| `username`    | String          | nullable (unauthenticated relays allowed)                   |
| `password_enc`| LargeBinary     | nullable; Fernet-encrypted SMTP password                    |
| `from_email`  | String          | global default sender address                               |
| `from_name`   | String          | default `""`; display name (newlines stripped)             |
| `updated_at`  | DateTime(tz)    | `server_default=now()`, `onupdate=now()`                    |

Not tenant-scoped → **no RLS**; only the owner (worker) and superadmin-gated API touch it.

### New table: `report_schedule` (tenant-scoped, RLS)

Migration `0022`. RLS enabled + forced + tenant policy (same pattern as `generated_reports`).

| column         | type                | notes                                                       |
|----------------|---------------------|-------------------------------------------------------------|
| `id`           | UUID PK             |                                                             |
| `tenant_id`    | UUID FK→tenants     | `ON DELETE CASCADE`; RLS key                                |
| `device_id`    | UUID FK→devices     | nullable; `ON DELETE CASCADE`; NULL = tenant scope          |
| `enabled`      | Boolean             | default `true`                                              |
| `frequency`    | String              | `weekly` \| `monthly` \| `on_demand`                        |
| `weekday`      | Integer             | nullable; `0=Mon..6=Sun`; set iff `frequency='weekly'`; `CHECK (weekday BETWEEN 0 AND 6)` |
| `hour`         | Integer             | `CHECK (hour BETWEEN 0 AND 23)`; UTC send hour (ignored for `on_demand`) |
| `recipients`   | ARRAY(String)       | email addresses (validated + capped in the service)         |
| `next_run_at`  | DateTime(tz)        | nullable; when `<= now` and `enabled`, the schedule fires; **always NULL for `on_demand`** |
| `last_run_at`  | DateTime(tz)        | nullable                                                     |
| `created_by`   | UUID                | nullable                                                    |
| `created_at`   | DateTime(tz)        | `server_default=now()`                                      |
| `updated_at`   | DateTime(tz)        | `server_default=now()`, `onupdate=now()`                    |

Uniqueness (Postgres treats NULLs as distinct, so two partial indexes):
- `uq_report_schedule_tenant`  UNIQUE on `(tenant_id)` WHERE `device_id IS NULL` — one tenant schedule.
- `uq_report_schedule_device`  UNIQUE on `(tenant_id, device_id)` WHERE `device_id IS NOT NULL` — one
  schedule per device.
- Index `ix_report_schedule_due` on `(enabled, next_run_at)` for the worker's due query.

### Column add: `report_settings.from_email`

`from_email String NOT NULL DEFAULT ''` — per-tenant sender override (empty ⇒ use the global
`smtp_settings.from_email`).

### Column add: `generated_reports.device_id`

`device_id UUID NULL` FK→devices `ON DELETE SET NULL` — records which device a scheduled per-device
report covered (NULL for tenant/fleet reports). Lets the per-device history be filtered later.

## Data flow

### Configuration

- **SMTP (superadmin):** `GET /api/admin/smtp` → current config **without the password** (returns
  `has_password: bool`). `PUT /api/admin/smtp` → upsert; password optional (omitted ⇒ keep existing,
  empty string ⇒ clear). `POST /api/admin/smtp/test` `{to}` → send a test email using the submitted
  config (password falling back to stored) **without persisting**; returns `{ok, detail}`.
- **Schedules (tenant_admin):** under `/api/tenants/{tenant_id}/report-schedules`:
  `GET` (list tenant + device schedules), `PUT` (upsert one schedule — body carries optional
  `device_id`, `enabled`, `frequency`, `weekday`, `hour`, `recipients`), `DELETE /{schedule_id}`, and
  `POST /{schedule_id}/send-now` (manual on-demand delivery — works for any frequency).
- **Sender + language (tenant_admin):** fold `from_email` into the existing
  `GET/PUT /reports/settings` (`ReportSettingsIn/Out`); the language picker is already backed by
  `language` + `GET /reports/languages`.

### Delivery (worker)

Generation and sending are **split into two jobs** so a send retry never regenerates the PDF:

1. **`enqueue_due_reports` (cron, hourly at minute 0):** runs as owner; `SELECT * FROM report_schedule
   WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= now()` (skips `on_demand`, whose
   `next_run_at` is NULL). For each, enqueue `deliver_scheduled_report(schedule_id)`.
2. **`deliver_scheduled_report(schedule_id, *, manual=False)` (job — generate + store + advance):**
   - Load the schedule. When `manual=False`, re-check `enabled` and `next_run_at <= now` (skip stale
     cron duplicates). Load the tenant; if `device_id` set, load the device (if deleted: disable the
     schedule, audit, return).
   - **Window** (UTC, aligned to day boundaries):
     - `weekly` (and `on_demand` send-now): `to = today 00:00`, `frm = to − 7 days`.
     - `monthly`: the **previous calendar month** — `to = first of this month 00:00`,
       `frm = first of previous month 00:00`.
   - **Build:** `ReportService(session, tenant_id).build_report(tenant_name, frm, to, locale,
     device_id=schedule.device_id)`. `device_id=None` ⇒ fleet; set ⇒ that device only.
   - **Store:** `GeneratedReportRepository.create(kind="scheduled", device_id=…, period_from=frm,
     period_to=to, created_by=None, pdf=…)` → `generated_report_id`.
   - **Advance the cadence now** (independent of send success): set `last_run_at = now`; for a cron run
     (`manual=False`) of a `weekly`/`monthly` schedule, `next_run_at = _advance(frequency, weekday,
     hour, after=now)`. A `manual` send and `on_demand` schedules never touch `next_run_at`.
   - **Enqueue the send:** `send_report_email_job(generated_report_id, schedule_id, attempt=1)`.
3. **`send_report_email_job(report_id, schedule_id, attempt)` (job — send + retry):**
   - Resolve `smtp_settings` (must be `enabled`; else **defer** the send by retry — operator may be
     mid-setup); `from_email = report_settings.from_email or smtp_settings.from_email`. Load recipients
     from the schedule (skip if empty, audit). Load the stored PDF by `report_id`.
   - **Send:** `EmailService.send(...)` with subject `"{title} — {tenant|device} — {frm:%Y-%m-%d}…"`,
     a short text body, the PDF attachment.
   - **On success:** audit `report.schedule.delivered`.
   - **On `EmailSendError`:** if `attempt < MAX_SEND_ATTEMPTS` (=12), re-enqueue
     `send_report_email_job(report_id, schedule_id, attempt+1)` with `_defer_by=RETRY_INTERVAL`
     (10 min) → retries span ≈2 h. Else audit `report.schedule.failed` (give up). Cadence already
     advanced in step 2, so the next scheduled run is unaffected by send retries.
4. **`next_run_at` initialisation (`_advance`):** when a `weekly`/`monthly` schedule is created/enabled
   or its frequency/weekday/hour changes, set `next_run_at` = the next **future** occurrence:
   `weekly` → next date matching `weekday` at `hour` (today if it is `weekday` and `hour` not yet
   passed, else the following week); `monthly` → next 1st-of-month at `hour`. `on_demand` →
   `next_run_at = NULL`. Disabling leaves the row; the cron skips disabled rows.

**On-demand / "send now":** `POST /report-schedules/{id}/send-now` enqueues
`deliver_scheduled_report(schedule_id, manual=True)` — fresh build + store + send (with the same retry
chain), without touching `next_run_at`. Works for every frequency.

### Report scope (single device)

`build_context(aggregator, *, device_id=None, …)`: when `device_id` is given, restrict the section
loop to that one device (`aggregator.device(device_id)` → a single `DeviceRow`, or none ⇒ empty
report). All existing per-device aggregator calls already pass `device_id`, so no query changes.

## Email transport

`app/services/email/smtp.py`:

- `@dataclass SmtpSendConfig(host, port, security, username, password, from_email, from_name)`.
- `async def send_report_email(cfg, *, subject, recipients, body_text, attachment)` where
  `attachment = (filename, bytes, "application/pdf")`. Builds an `email.message.EmailMessage`
  (headers via the stdlib API ⇒ no header injection), sets `From`/`To`/`Subject`, attaches the PDF.
- Transport per `security`: `starttls` (aiosmtplib `start_tls=True`), `tls` (`use_tls=True`, implicit,
  typically port 465), `none` (plain). Authenticate only when `username` is set.
- Failures raise `EmailSendError(str)`; callers (worker job, test endpoint) handle it. The worker's
  `send_report_email_job` retries on `EmailSendError` every `RETRY_INTERVAL=600s` up to
  `MAX_SEND_ATTEMPTS=12` (≈2 h) via arq `_defer_by`.
- Add `aiosmtplib` to `pyproject.toml` dependencies.

## Security

- **SMTP password** encrypted at rest (Fernet via `app.core.crypto`); **never** returned by any GET
  (write-only; GET exposes only `has_password`). Test endpoint accepts a plaintext password in the
  body (same trust level as PUT) and never persists it.
- **SMTP config + test are superadmin-only** (`require_org(Action.USER_MANAGE)`), CSRF-protected. The
  test send is a deliberate outbound action by a fully-trusted org owner; it is **audited**
  (`smtp.test`) and the host is whatever the superadmin enters (no SSRF gain beyond their existing
  trust). Rate-limit note: rely on superadmin scarcity; no extra limiter in v1.
- **Recipients** validated with `email-validator`, de-duplicated, capped (`MAX_RECIPIENTS = 50`) at
  both schedule write and test time. `from_name`/`from_email` newline-stripped.
- **Schedule writes** gate on `REPORT_CONFIG` (tenant_admin) + CSRF; reads on `DEVICE_VIEW`. A
  device-scoped schedule's `device_id` is validated to belong to the tenant.
- **Worker** runs as owner (bypasses RLS) but scopes every query by explicit `tenant_id`
  (`device_id`), exactly like the existing report cron.
- **PDF size** is already bounded by `MAX_RANGE_DAYS`; attachments stay small.

## Error handling

| Condition                                   | Behaviour                                               |
|---------------------------------------------|---------------------------------------------------------|
| SMTP not `enabled` / unconfigured at send   | `send_report_email_job` treats it like a transient failure → retries every 10 min (≤2 h); the cadence already advanced, so the next run is unaffected |
| Schedule's device deleted                   | `deliver_scheduled_report` disables the schedule (`enabled=False`), audits, returns |
| SMTP send fails (transport/auth)            | report stays stored; **retry every 10 min up to 2 h** (`MAX_SEND_ATTEMPTS=12`); on final give-up `report.schedule.failed` audited |
| Invalid recipients / frequency / weekday / hour | rejected at API write (422/400); `weekly` requires `weekday`, `monthly`/`on_demand` reject one |
| Test send fails                             | `POST /smtp/test` returns `{ok:false, detail}` (no 5xx) |
| Empty per-device report (device has no data)| renders an empty section; still sent (consistent with today's empty fleet reports) |
| Two cron fires before a job runs (dup)      | `deliver_scheduled_report` re-checks `next_run_at <= now` (cron path) and advances idempotently |

## Testing

- **Email transport:** unit-test `send_report_email` against an in-process aiosmtplib test server (or a
  mock transport) for each `security` mode + auth on/off; assert MIME structure (PDF attachment,
  headers) and that `EmailSendError` wraps transport failures.
- **Schedule model/repo:** upsert tenant vs device rows; partial-unique constraints (one tenant row,
  one per device); `_advance` math for `weekly` (next chosen weekday at hour, always future),
  `monthly` (next 1st-of-month at hour), `on_demand` (`next_run_at` stays NULL); window math (weekly =
  prior 7 days; monthly = previous calendar month).
- **Worker jobs:** `deliver_scheduled_report` (monkeypatched send) — build → store (`GeneratedReport`
  with right `kind`/`device_id`/window) → cadence advance → enqueues the send; plus skip paths (device
  deleted, disabled, stale cron dup). `send_report_email_job` — success audits delivered; failure
  retries with `attempt+1`/`_defer_by=600` up to `MAX_SEND_ATTEMPTS`, then audits failed; SMTP-off
  behaves as a transient retry. `manual=True` (send-now) builds + sends without advancing `next_run_at`.
- **Single-device report:** `build_context(device_id=…)` yields exactly one section; `device_id=None`
  unchanged (all devices).
- **API:** SMTP GET hides the password; PUT upsert + password keep/clear; test ok/fail; schedule
  GET/PUT/DELETE/send-now RBAC (tenant_admin write, read_only denied) + CSRF + device-belongs-to-tenant
  + email validation + recipient cap + frequency/weekday validation (`weekly` needs `weekday`).
- **Frontend:** superadmin SMTP page (form + test button states); tenant schedule editor (tenant +
  device schedules, recipients chips, sender override, language picker). `npm run build` must pass
  (`tsc -b` checks tests — see the frontend-build-gate learning).

## Frontend

- **Superadmin → SMTP** (new page under the existing admin/settings area): host/port/security/username/
  password (write-only; shows "configured" when `has_password`)/from_email/from_name/enabled + a
  **"Send test email"** button (prompts for a recipient, shows ok/error inline).
- **Tenant → Report settings** (extend existing page): add `from_email` (sender override) + the
  **language picker** (from `GET /reports/languages`).
- **Tenant → Report schedules** (new section/page): the tenant (fleet) schedule + a per-device list;
  each row = enabled toggle, frequency picker (**Weekly** + weekday select / **Monthly** / **On
  demand**), hour, recipients (multi-email chips), and a **"Send now"** button. Reuse Mantine v9 + the
  Midnight-NOC design tokens.

## Build phases (informs the plan; one cohesive subsystem)

- **Phase A — SMTP + tenant delivery:** `smtp_settings` table/model/service + crypto; `EmailService` +
  `aiosmtplib`; superadmin SMTP API + test; `report_schedule` table/model/repo (both scopes, but wire
  the **tenant** path end-to-end first) with the `weekly`/`monthly`/`on_demand` frequency + `_advance`;
  `report_settings.from_email`; worker `enqueue_due_reports` + `deliver_scheduled_report` +
  `send_report_email_job` (retry chain), replacing the weekly cron; tenant schedule API incl.
  `send-now`; frontend SMTP page + tenant schedule editor (frequency picker + send-now) + sender/
  language fields.
- **Phase B — per-device scope:** `generated_reports.device_id`; `build_context(device_id=…)` +
  `aggregator.device()`; device-scoped schedule rows end-to-end (already in the table) + the frontend
  per-device schedule list; tests for the device path.

## Out of scope / future TODO

- **OAuth sending** (Gmail / M365) instead of an SMTP password — recorded; not built.
- Emailing the existing **on-demand download** report (the `/reports` POST) — stays download-only;
  "send now" lives on a saved schedule (with its scope + recipients), not on the ad-hoc generator.
- Per-recipient delivery status / bounce handling, DKIM/SPF setup — operator's SMTP relay concern.
```

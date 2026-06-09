# OPNGMS — Phase 5 / Milestone 5A: Reporting Foundation & Engine — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (design); the user approved the Phase 5 decomposition and the 5A design and authorized proceeding
- **Phase:** 5 of 5 — Milestone 5A (the PDF reporting engine; first reporting milestone)
- **Depends on:** 3A (IDS/Suricata events), 3C (events query API + `EventRepository`), 2 (metrics), RLS, RBAC, AuditService, CSRF — all in `main`
- **Enables:** 5B (web/bandwidth/status sections), 5C (applications/web-filter + threat-level), 5D (white-label config), 5E (scheduled reports + history)

---

## 1. Context

**Phase 5** produces per-customer **white-label PDF reports** (the MSP deliverable: attacks, sites
visited, bandwidth). The reference layout (a ~44-page MSP report) is emulated in spirit, not
pixel-perfect: a title page, a table of contents, then **one section per firewall** built from
*chart + ranked-table* blocks (Data Usage, Applications, Web Activity, Web Filter, Attacks, Up/Down
status), and a footer (page numbers, timezone, report owner).

Phase 5 is **decomposed into milestones** (each ships a working, testable artifact):

| Milestone | Scope | Data |
|-----------|-------|------|
| **5A** *(this spec)* | Reporting engine (WeasyPrint + Jinja2 + server-side SVG charts), aggregation layer, on-demand generate API, the document skeleton, and the **Attacks** section as the first real block | IDS (3A) |
| 5B | Web Activity (DNS), Data Usage/bandwidth (interface metrics), Up/Down status sections | DNS (3B) + metrics (2) |
| 5C | Applications + Web Filter sections with labeled **mock** data + threat-level color coding | mock |
| 5D | Per-tenant white-label config (logo upload, owner, title, timezone, default range) + settings UI | — |
| 5E | Scheduled periodic generation (ARQ cron) + generated-report storage + history list + download UI | — |

**5A builds the engine end-to-end safely**: a real, tested data→chart→table→PDF pipeline, proven with
the Attacks section, with the document skeleton (title/TOC/footer) ready for 5B–5C to fill in.

## 2. Design decisions (Phase 5 brainstorming)

| Topic | Decision |
|-------|----------|
| v1 section scope | **Full 6-section template**; sections without ingest (Applications) carry **labeled mock data** (5C). 5A delivers the skeleton + Attacks. |
| Generation/delivery | **On-demand (API + UI) AND a periodic ARQ cron** — 5A builds the on-demand API + engine; the cron is 5E. |
| White-label | **Full per-tenant white-label** (logo/owner/title/timezone) — the config + UI is **5D**; 5A renders a skeleton with branding **placeholders** (tenant name, range, timezone) and a branding context object ready for 5D. |
| PDF tech | **WeasyPrint** (HTML/CSS → PDF) + **Jinja2** templates (autoescape ON) + **server-side SVG charts** (lightweight, vectorial, no matplotlib). |
| Charts | Hand-built **SVG** (pure functions: data → SVG string) for bar and line/timeline charts — crisp vector in the PDF, fully CSS-styleable, zero heavy deps. |
| Storage | 5A returns the PDF inline (download); **persisted report history is 5E**. No new table in 5A. |
| Authorization | New RBAC action **`REPORT_GENERATE`** (generating a report reads all tenant data) granted to `tenant_admin` + `operator`; audited. |

## 3. Module layout (`app/services/reporting/`)

```
app/services/reporting/
  __init__.py
  aggregation.py   # tenant-scoped queries: ranked tops (reuse EventRepository.top) + time-bucketed timelines
  charts.py        # pure functions: data -> SVG string (bar_chart, line_chart/timeline)
  context.py       # build the ReportContext (title page, per-device sections, footer) from aggregations
  template.py      # Jinja2 Environment (autoescape) + render_html(context) -> str
  service.py       # ReportService.build_report(tenant_id, frm, to) -> bytes (PDF)
  templates/
    report.html.j2 # the document: title page, TOC, per-firewall section frame, footer
    report.css     # print CSS: @page (size/margins), running header/footer, page numbers, section page-breaks
```
- `app/api/reports.py` — the on-demand endpoint (router mounted under the tenant prefix).
- `app/schemas/report.py` — request/response Pydantic models (date range, options).

## 4. Data flow

1. **API** `POST /api/tenants/{tenant_id}/reports` (body: `{ from, to }`, optional `device_ids`), gated
   by **`REPORT_GENERATE`** + CSRF; audited (`report.generate`). Runs as `opngms_app` → RLS scopes every
   read to the tenant.
2. **`ReportService.build_report(tenant_id, frm, to)`**:
   - Resolve the tenant's devices (RLS-scoped); for each device, aggregate the **Attacks** block from IDS
     events: timeline (time-bucketed counts) + ranked tables (Top Attempts by signature `name`, Top
     Targets by `dst_ip`, Top Initiators by `src_ip`).
   - Build the **`ReportContext`** (branding placeholders: tenant name, date range, timezone, owner=None;
     TOC entries; per-device sections; footer).
   - Render charts to SVG (`charts.py`), inline them into the context.
   - `template.render_html(context)` (Jinja2, **autoescape**) → HTML string referencing only inline SVG +
     the local `report.css`.
   - `WeasyPrint.HTML(string=html, url_fetcher=_blocked_fetcher).write_pdf()` → `bytes`.
3. **Response:** `application/pdf` (`Content-Disposition: attachment; filename=...`), inline bytes.

## 5. Aggregation layer (`aggregation.py`)

- **Ranked tops:** reuse the existing `EventRepository.top(field, source, frm, to, limit)` (3C) for Top
  Initiators (`src_ip`), Top Targets (`dst_ip`), Top Attempts (`name`), filtered `source="ids"`.
- **Timeline:** add a tenant-scoped time-bucketed count query using TimescaleDB `time_bucket($interval,
  time)` over `events` (filtered by source + range), returning `[(bucket_start, count)]`. Bucket width is
  derived from the range (e.g. hourly for ≤2 days, daily otherwise) — a small helper picks it.
- All queries are tenant-scoped (RLS handles isolation; the repo also filters `tenant_id`). No secret
  fields are read.

## 6. Charts (`charts.py`)

- Pure functions returning **SVG strings** (no I/O, no global state):
  - `line_chart(points, *, width, height, ...)` — the timeline (x=time bucket, y=count).
  - `bar_chart(rows, *, ...)` — optional ranked-bar visual (tables are the primary ranked view).
- Deterministic output (testable by asserting structural SVG content). Colors via CSS classes where
  possible so the print CSS can theme them. No remote fonts/resources.

## 7. Template & print CSS

- `report.html.j2`: title page (tenant name, report title, date range), TOC, a loop over device sections
  (each: section heading, the Attacks block = inline SVG timeline + ranked tables), and a footer.
- `report.css`: `@page { size: A4; margin: ... }` with `@bottom-center`/`@bottom-right` running elements
  for **page numbers** + "Report generated for timezone …" + "Report owner …"; `page-break-before` per
  firewall section; table/threat-level color classes (threat-level used from 5C).
- **Autoescape ON** for all interpolated data (DNS hostnames, IDS signatures, IPs are untrusted input).

## 8. Security & safety

- **HTML/CSS injection:** Jinja2 `autoescape=True` for all report data (signatures, hostnames, names are
  attacker-influenced). No `| safe` on report data; only our own generated SVG (built from escaped values)
  is marked safe.
- **SSRF via WeasyPrint:** a custom `url_fetcher` that **refuses all network/remote URLs** (and only
  permits explicitly-allowlisted local asset paths) so a malicious URL embedded in report data or (later)
  a logo cannot trigger an outbound fetch. No `base_url` that resolves remote.
- **RBAC:** `REPORT_GENERATE` (tenant_admin + operator) on the generate endpoint; **CSRF** on the POST;
  **audited** (`report.generate`).
- **Tenant isolation:** runs as `opngms_app` under RLS; a report contains only the tenant's data; a
  cross-tenant isolation test (real `opngms_app`) proves no leakage. A `device_id` from another tenant →
  404/empty (RLS-hidden).
- **No config secrets** in reports (reports read events/metrics, never config payloads).
- **Resource bounds:** ranked tables capped (`limit`); timeline bucket count bounded; a sane max range to
  avoid unbounded queries.

## 9. Dependencies

- Add to `pyproject.toml`: **`weasyprint`**, **`jinja2`**. (SVG is hand-built — no chart lib.) WeasyPrint
  pulls system libs (pango/cairo/gdk-pixbuf) — note for the **Deploy** milestone Dockerfile. No DB
  migration in 5A.

## 10. Milestone 5A breakdown (for the plan)
1. **Deps + skeleton**: add weasyprint/jinja2; create `app/services/reporting/` package; a trivial
   `template.render_html` + `report.html.j2`/`report.css` rendering a title page + footer; a
   `ReportService.build_report` that returns a valid `%PDF`; unit test (valid PDF, title present).
2. **Charts**: `charts.py` `line_chart`/`bar_chart` pure SVG + tests (deterministic structure, values
   escaped).
3. **Aggregation**: `aggregation.py` ranked tops (reuse `EventRepository.top`) + `timeline` (time_bucket)
   + bucket-width helper; tenant-scoped tests + **cross-tenant isolation** test (real `opngms_app`).
4. **Context + Attacks section**: `context.py` assembles `ReportContext` (branding placeholders, TOC,
   per-device Attacks block); template renders timeline SVG + Top Attempts/Targets/Initiators tables;
   test the rendered HTML/PDF contains the seeded Attacks data and **no** other tenant's data.
5. **API**: `POST /api/tenants/{tenant_id}/reports` (`REPORT_GENERATE` + CSRF + audit), returns the PDF;
   RBAC/CSRF/cross-tenant 404 tests; **SSRF test** (hostile URL in data is not fetched).

## 11. Definition of "Done" (5A)
- An operator can generate, on-demand, a PDF for a tenant + date range with a **title page**, **TOC**, a
  real **Attacks** section per firewall (timeline chart + Top Attempts/Targets/Initiators ranked tables),
  and a **footer** (page numbers, timezone, owner placeholder).
- Tenant-scoped + RLS-isolated, gated by **`REPORT_GENERATE`** + CSRF + audited; SSRF-safe (no remote
  fetch); data autoescaped; no config secrets.
- Suite green + `alembic check` clean (no new table in 5A).

## 12. Non-goals (5A) / deferred
- **Web Activity / Bandwidth / Up-Down** sections (5B), **Applications / Web Filter / threat-level** (5C).
- **White-label config model + UI** (5D) — 5A uses branding *placeholders* + a branding context hook.
- **Scheduled generation + report storage/history + download UI** (5E) — 5A returns the PDF inline; no
  persistence.
- **Frontend** — 5A is backend/engine only; the generate **UI button** lands with 5E (or alongside 5B).

## 13. Open questions (non-blocking)
- **Bucket-width heuristic** — pick by range span (hourly ≤2d, 6-hourly ≤14d, daily otherwise); tune in 5B.
- **Chart styling** — minimal, clean SVG in 5A; richer theming when the white-label palette lands (5D).
- **Max report range** — cap (e.g. 92 days) to bound query cost; confirm during 5A implementation.

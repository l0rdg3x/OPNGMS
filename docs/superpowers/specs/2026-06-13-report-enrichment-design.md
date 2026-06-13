# Report enrichment — design

**Date:** 2026-06-13 · **Status:** Approved (user: "tutti e 4", toggles "per tenant e device", client-friendly defaults)

## Goal

Enrich the per-tenant PDF report with **4 new sections**, each independently **include/exclude-able via a
toggle at BOTH the tenant level (default) and the per-device level (override)**, with defaults tuned so a
client-facing report leads with the reassuring/value sections and hides the deep-technical ones.

## The 4 new sections (all grounded in telemetry the app already collects)

| Section | Placement | Audience | Default | Data source |
|---------|-----------|----------|---------|-------------|
| **Executive summary** (KPIs: devices managed/online, attacks blocked, data transferred, uptime %, alerts in period, updates pending) | **report-level** (top, after TOC) | client | **ON** | aggregate of the existing per-device aggregations + alerts + firmware |
| **Per-device health** (CPU / memory / disk avg+peak, sparkline) | per-device (`DeviceSection`) | MSP/technical | **OFF** | `metrics` hypertable (`cpu.pct`, `mem.pct`, `disk.pct`) via a new aggregator accessor |
| **Alerts + WAN/gateway quality** (alerts raised in the period w/ severity+duration; per-gateway RTT/loss/availability; VPN up) | per-device | mixed (WAN uptime = client; alert internals = MSP) | **ON** | `alerts` table + `metrics` (`gateway.rtt_ms`/`loss_pct`/`up`, `vpn.up`) |
| **Firmware + config changes** (current firmware/edition + updates pending; config changes applied in the period) | per-device | MSP value | **ON** | `devices` (firmware_version/edition) + `config_change`/`config_snapshot` count in range |

The existing sections (attacks, web activity, data usage, up/down status, applications, web filter) also
become toggleable for consistency — same mechanism, all default ON (current behaviour).

## Toggle model (tenant default + per-device override)

A single source of truth, JSONB, so adding a section later needs no migration:
- **`report_settings.sections`** (`JSONB`, tenant default) — a `{section_key: bool}` map. Missing key =>
  the built-in default for that key. This is the **tenant-level** toggle set.
- **`report_schedule.sections`** (`JSONB`, nullable) — a per-schedule override. Since `report_schedule`
  rows are either tenant-scoped (`device_id IS NULL`) or **device-scoped** (`device_id` set), a
  device-scoped schedule's `sections` IS the **per-device** toggle override. `NULL` => inherit the tenant
  default.

**Resolution at generation time:** `effective = {**BUILTIN_DEFAULTS, **(settings.sections or {}),
**(schedule.sections or {})}`. A section is rendered iff `effective[key]` is truthy. "Send now" /
on-demand generation (no schedule) resolves against `BUILTIN_DEFAULTS + settings.sections`.

Section keys: `summary, health, alerts_wan, firmware_config` (new) + `attacks, web, data, status,
applications, web_filter` (existing). `BUILTIN_DEFAULTS`: client-friendly — `summary/alerts_wan/
firmware_config/attacks/web/data/status` ON; `health/applications/web_filter` OFF by default (technical
/ sample). (Note: applications & web_filter are still sample-data today, so OFF is sensible.)

Migration **0030** adds the two `JSONB` columns (`server_default '{}'` / nullable).

## Backend

- **`aggregation.py`** — add accessors: `health_summary(device_id, range)` (avg+peak cpu/mem/disk),
  `gateway_quality(device_id, range)` (per-gateway rtt/loss/up%), `alerts_in_range(device_id, range)`
  (from `alerts`), `firmware_info(device)` + `config_changes_in_range(device_id, range)` (count + list
  from `config_change`/`config_snapshot`), and a tenant-level `kpis(range)` for the executive summary.
- **`context.py`** — new dataclasses: `ExecutiveSummaryBlock` (report-level field on `ReportContext`),
  and per-device `HealthBlock`, `AlertsWanBlock`, `FirmwareConfigBlock` fields on `DeviceSection`. Each
  block is `None` when its section is toggled OFF (so the template skips it). `build_context` takes the
  resolved `sections: dict[str,bool]` and only builds the enabled blocks.
- **`template.py` / `templates/report.html.j2` / `report.css`** — render the executive summary band at the
  top and each new per-device block when present; charts reuse `charts.line_chart` / hand-built SVG. RTL
  + the 12-locale labels apply (Arabic etc.).
- **`i18n.py`** — add the NEW report strings (section titles, KPI labels, column headers) to **all 12
  report locales** (`_EN.._JA`); the guard test enforces parity.
- **Service/worker** — `service.py` resolves `effective sections` from settings (+ the schedule when a
  scheduled report) and passes it to `build_context`.

## API + Frontend

- **Schemas/endpoints** — `report_settings` GET/PUT gains `sections: dict[str,bool]`; `report_schedule`
  create/update gains `sections: dict[str,bool] | null`.
- **Frontend** — Report-settings page: a "Report sections" group of switches (tenant default). The
  per-device schedule editor (Report-schedule page): an optional per-device override of the same switches
  (a "use tenant default / customize" affordance). New `report:` i18n keys for the switch labels (en + 11).

## Testing

- Aggregation accessors (pure-ish, DB-backed) with seeded metrics/alerts/config-changes.
- Resolution: `BUILTIN_DEFAULTS` < tenant `settings.sections` < device `schedule.sections`.
- Report engine: a section toggled OFF is absent from the rendered HTML; ON is present; executive
  summary renders report-level; per-device blocks render under the right device.
- i18n parity guard covers the new keys across 12 locales; `report_text` returns translated new titles.
- API: settings/schedule round-trip the `sections` map; RBAC unchanged.

## Out of scope
- Per-section ordering/custom layout (fixed order). Charts for new sections kept simple (sparkline/bars).
- Historical config-change *diffs* in the PDF (just count + targets; the Config tab already has diffs).

# Service Events (Reliability) — Design Spec

**Date:** 2026-06-15
**Status:** Approved (design); writing the implementation plan next.
**Covers:** The first of the two "diagnostic-logs" milestones — surface **service crashes/restarts,
reboots, and disk/FS warnings** from the OPNsense **system log** as a per-device **reliability timeline**,
a fleet **Overview card**, a **report section**, and proactive **alerts**.

## Goal

OPNGMS already ingests security events (IDS/DNS) and perimeter signals (failed logins, firewall blocks)
from the OPNsense diagnostics-log API. It has no view of a device's **reliability**: did it reboot,
did a service crash/restart, is a filesystem filling up. This milestone classifies those events out of
the system log and surfaces them per-device (timeline tab), across the fleet (Overview card), in the
client report (a Reliability section), and as alerts.

## Verified facts (real box 192.168.1.82, read-only)

- `POST diagnostics/log/core/system` returns paged rows `{timestamp, parser, facility, severity,
  process_name, pid, rnum, line}` (same shape as the audit log already used for `auth_failures`).
  Observed processes on an idle box: `opnsense`, `dhcp6c`, `dhclient`. The lines that matter (reboot,
  crash, disk-full) are **sparse on an idle box** — their exact text needs runtime verification (below).
- The events pipeline `app/services/ingest.py` is **source-pluggable**: `SOURCES = ["ids","dns"]`, a cursor
  per `(device, source)` (`ingest_cursor`), and an idempotent `ON CONFLICT DO NOTHING` insert into the
  `events` hypertable. Adding a source = add to `SOURCES` + a `client.get_<source>()` + a `_fetch` branch.
- The events query API already filters by `source` AND `device_id` with keyset pagination
  (`GET /events?source=&device_id=&from=&to=&after=&limit=`) and aggregates (`GET /events/top?field=`),
  gated by `Action.DEVICE_VIEW`. **The per-device timeline needs no new query API.**
- Report sections are a registry: `SECTION_KEYS` tuple + `BUILTIN_DEFAULTS` dict + per-section builders
  (`app/services/reporting/sections.py` + the report service). Precedent: `attacks`, `failed_logins`,
  `firewall_blocks` are all event-derived sections.
- Alerts are `Alert(tenant_id, device_id, type, label)` (`app/services/alerting.py`); `evaluate_alerts`
  is poll-state-based — service-event alerts are raised at **ingest** time instead (a different trigger).

## Locked decisions (from brainstorming)

1. **Source = the system log** (`diagnostics/log/core/system`), pulled like `auth_failures`/`firewall_blocks`.
2. **Selective + fail-safe classification.** A curated rule set maps `(process_name, severity, line)` to a
   reliability event in one of **three categories**: `reboot`, `service` (crash/restart), `disk` (disk/FS
   warnings). **Only classified lines are stored**; an unrecognized line is skipped (we do NOT mirror the
   whole system log — that is the log-lake's job). Keeps the `events` table and the timeline meaningful.
3. **Storage = the existing `events` hypertable**, `source = "service"`. No schema change, no migration.
   Reuses the per-`(device, service)` cursor, the `ON CONFLICT` dedup, and the `events` retention store.
4. **v1 scope = timeline + Overview card + report section + alerts** (the full package).
5. **Alerts at ingest:** a new high-severity service event (a reboot, a service crash, a filesystem-full)
   raises a deduped `Alert`. Best-effort; never blocks ingest.
6. **RUNTIME VERIFICATION:** the exact line patterns for reboot/crash/disk events can't be confirmed
   against an idle box. The classifier ships as a **curated, extensible** rule set; the operator verifies
   it against real events (trigger a service restart / reboot) and the set is tuned in follow-ups — same
   "RUNTIME VERIFICATION REQUIRED" posture as the curated connector kinds.
7. **Out of scope:** gateway/WAN flaps (overlaps with the existing monitoring), and any category beyond
   the three.

## Architecture

```
 worker per-device cycle ──▶ ingest_events(... "service") ──▶ client.get_service_events(since)
                                       │                          POST diagnostics/log/core/system
                                       ▼                          (matrix-resolved, paged)
        parse_service_events(rows): classify -> [{time, category, name, severity, attributes, event_key}]
                                       │  (unrecognized lines dropped)
                                       ▼
        events hypertable (source="service")  ──┬──▶  GET /events?source=service&device_id=  (timeline tab)
                                                 ├──▶  Overview card aggregate (fleet 24h)
                                                 ├──▶  report "reliability" section (period rollup)
                                                 └──▶  raise Alert on a new high-severity event
```

## Component 1 — Connector capability + classifier (backend)

- **`app/connectors/opnsense/profiles.py`** — add a `service_events` capability to the CAPABILITY map:
  `_POST("diagnostics/log/core/system", {"current":1,"rowCount":MAX_QUERY_ROWS,"searchPhrase":""})` →
  `parsers.parse_service_events`. Matrix entry mirrors `auth_failures`.
- **`app/connectors/opnsense/client.py`** — `get_service_events(since=None)` via `_capability("service_events")`
  (mirror `get_auth_failures`).
- **`app/connectors/opnsense/parsers.py`** — `parse_service_events(data) -> list[dict]`. A curated,
  ordered `RULES` list of `(category, name, base_severity, predicate)` where the predicate matches on
  `process_name` and/or a compiled `line` regex (and may read the row `severity`). For each row, the first
  matching rule yields `{time, category, name, severity, event_key, attributes:{process, message,
  log_severity}}`; non-matching rows are skipped. `event_key = event_key(ts, name, line-hash)` for dedup.
  Starter rules (to verify/tune at runtime):
  - **reboot** — boot/shutdown markers (e.g. a kernel boot banner / `reboot`/`shutdown` invocation).
  - **service** — a daemon crash (`exited on signal`, `core dumped`) or restart (configd "restarting"/
    "started").
  - **disk** — `No space left on device` / `filesystem full` / SMART / ZFS-degraded patterns.
  Severity mapping: emerg/alert/crit/err → `high`; warning → `medium`; else the rule's base severity.

## Component 2 — Ingest + alerts (backend)

- **`app/services/ingest.py`** — add `"service"` to `SOURCES`; a `_fetch` branch
  `client.get_service_events(since)`. `_normalize` already carries the generic fields (src_ip="" for these).
- **Alerts** — after the service source is ingested, for each NEW high-severity event raise a deduped
  `Alert(type="service_event", label=f"{name} on {device.name}")` (reuse the open-alert dedup in
  `alerting.py`). High-severity = `reboot`, a service crash, or a disk-full. Wrapped so an alert failure
  never aborts ingest. (Implementation: return the new high-severity rows from the service ingest and
  raise alerts in the same transaction, or a small `evaluate_service_alerts` helper.)

## Component 3 — Frontend (device tab + Overview card)

- **Device page** — a new **"Reliability"** tab: a paginated timeline calling the existing
  `GET /events?source=service&device_id=<id>` (keyset `after` cursor, from/to range), rendering
  time · category · name · severity · process/message. Mirror the existing events/log list components.
- **Overview** — a **summary card**: fleet service-event count (last 24h) and/or count of devices with a
  recent reboot/crash. Backed by `GET /events/top?field=category&source=service` or a small aggregate;
  reuse the perimeter Overview-card pattern.
- **i18n** — all new strings in `en.ts` mirrored across the 12 locales (compiler-enforced parity).

## Component 4 — Report section (backend + PDF)

- **`sections.py`** — add `"reliability"` to `SECTION_KEYS` and a `BUILTIN_DEFAULTS` entry (default **on**,
  alongside the other value sections).
- **Report builder + PDF** — a `reliability` section that, for the report period and the device set,
  rolls up service events (counts by category, notable events: reboots, crashes, disk warnings) and
  renders a section in the per-client PDF, following the `failed_logins`/`firewall_blocks` section
  precedent. Honors the standard toggle precedence (`BUILTIN_DEFAULTS < tenant < per-device/schedule`).

## Data model

No schema change, no migration: `source="service"` rows in the existing `events` hypertable; alerts in the
existing `alert` table; report toggle in the existing section model.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| System-log source unavailable on a device | `ingest_events` already skips a failing source without blocking others |
| Unrecognized log line | skipped by the classifier (only classified events stored) |
| Alert raise fails | caught; ingest still commits the events |
| Duplicate event across polls | `event_key` + `ON CONFLICT DO NOTHING` (idempotent) |
| Report section enabled but no events in range | renders an empty/"no events" section (like the other event sections) |

## Security

- Read-only system-log pull through the existing SSRF-guarded connector; no new outbound path, no secrets.
- The timeline/aggregate reuse the existing `Action.DEVICE_VIEW`-gated, tenant-scoped (RLS) events API —
  no new authz surface. Alerts and report sections are tenant-scoped like all others.
- The classifier stores only a bounded `{process, message, log_severity}` from each matched line — no
  credential material is parsed or logged.

## Testing

- **Parser/classifier (pure):** representative system-log rows for each category → the expected
  `{category, name, severity}`; noise rows → dropped; severity mapping; `event_key` stability/dedup.
- **Ingest:** `"service"` source wired; cursor advances; `ON CONFLICT` dedup across two polls; a failing
  source doesn't block others.
- **Alerts:** a new high-severity service event raises one deduped alert; a repeat doesn't; an alert
  failure doesn't abort ingest.
- **API reuse:** `GET /events?source=service&device_id=` returns the device's timeline, keyset-paginated
  (covered by the existing events tests; add a `source=service` case).
- **Report:** the `reliability` section renders with events and degrades to empty; toggle precedence holds.
- **Frontend:** the Reliability tab renders the timeline (mock the events API); the Overview card renders;
  `npm run build` green (i18n parity).

## Build phases (informs the plan)

- **PR1 — Backend ingest + alerts:** connector capability + `parse_service_events` classifier + `SOURCES`
  wiring + ingest-time alerts + tests.
- **PR2 — Frontend:** device Reliability tab (existing events API) + Overview card + i18n (12 locales) + build.
- **PR3 — Report section:** `reliability` section (builder + PDF + toggle) + tests.

## Out of scope / future

- The second diagnostic-logs milestone — **"Audit delle modifiche sul box"** (config-change audit) — is a
  separate spec/plan, next.
- Gateway/WAN flap events (monitoring overlap); categories beyond reboot/service/disk; tuning the
  classifier rule set is an ongoing, runtime-verified follow-up.

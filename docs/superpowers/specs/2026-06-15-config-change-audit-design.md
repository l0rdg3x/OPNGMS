# Config-change Audit ("Audit delle modifiche sul box") — Design Spec

**Date:** 2026-06-15
**Status:** Approved (design); writing the implementation plan next.
**Covers:** The second of the two "diagnostic-logs" milestones — surface **who/what/when changed the
OPNsense config**, with a **best-effort drift-cause attribution** of changes made **directly on the box**
(WebGUI / console / scripts) versus through the management **API**. Per-device **timeline tab**, fleet
**Overview card**, **report section**, and proactive **drift alerts**.

## Goal

OPNGMS pushes config changes through the OPNsense MVC API and records its own ledger (the superadmin
**Audit viewer**, v0.10.0). It has **no view of changes made on the box itself** — someone editing a rule
in the WebGUI, a console/SSH change, a script-driven change. For compliance ("who changed what, when") and
**drift-cause** visibility ("this box drifted from template — who touched it?"), this milestone ingests the
box's **config-change audit log** and surfaces it per-device, across the fleet, in the client report, and
as alerts on **direct (non-API) changes**.

This is **distinct from** the OPNGMS Audit viewer (v0.10.0): that is OPNGMS's *own* write-ledger (actions
OPNGMS took); this is the *box's* config-change log (changes seen from the device, whoever made them).
Named **"Config changes"** in the UI to avoid confusion with "Audit".

## Verified facts (real box 192.168.1.82, dedicated test box, live)

A live `POST diagnostics/log/core/audit` (the endpoint already used for `auth_failures`) returns paged
rows `{timestamp, parser, facility, severity, process_name, pid, rnum, line}`. Two `process_name` values
dominate: `configd.py` (action-allowed noise) and **`audit`** (the rows we want). The config-change rows
(`process_name == "audit"`, `severity == "Notice"`) have a **stable, parseable shape** — two real samples:

```
 user (root) changed configuration to /conf/backup/config-1781551620.8666.xml in /usr/local/opnsense/scripts/firmware/register.php /usr/local/opnsense/scripts/firmware/register.php made changes
 user root@192.168.6.100 changed configuration to /conf/backup/config-1781551587.0626.xml in /api/monit/settings/delTest/2f2d1f72-... /api/monit/settings/delTest/2f2d1f72-... made changes
```

Grammar: `user <ACTOR> changed configuration to <BACKUP_FILE> in <SRC_PATH> <SRC_PATH> made changes`, where:
- **`<ACTOR>`** is either `(<user>)` — a **local/script** change, no remote IP — or `<user>@<ip>` — a
  **remote** change carrying the source IP.
- **`<BACKUP_FILE>`** is `/conf/backup/config-<epoch>.xml`, **unique per save** → the dedup key.
- **`<SRC_PATH>`** is the request that wrote the config: `/api/<module>/<controller>/<action>[/<uuid>]`
  (the MVC API — how OPNGMS writes), a legacy WebGUI page (`/firewall_rules.php` …), or a system script
  under `/usr/local/opnsense/scripts/…`. The path's first meaningful segment gives the **area**
  (`firewall`, `ids`, `monit`, `interfaces`, `system`, …).

Reused infrastructure (unchanged): the `events` hypertable + source-pluggable `ingest.py`
(`SOURCES`, per-`(device, source)` cursor, `ON CONFLICT DO NOTHING`); the keyset-paginated, RLS-scoped,
`DEVICE_VIEW`-gated `GET /events?source=&device_id=` query API (timeline needs **no new query API**); the
report-section registry; ingest-time deduped `Alert`s (same trigger as the v0.15.0 service-event alerts).

## Locked decisions (from brainstorming)

1. **Source = the audit log** (`diagnostics/log/core/audit`), pulled like `auth_failures` — same endpoint,
   a **different `process_name == "audit"` line family** (config changes, not failed logins).
2. **Selective + fail-safe parsing.** Only lines matching the "changed configuration" grammar are stored;
   any other line (incl. `configd.py` noise, unrecognized audit lines) is **skipped** (we do NOT mirror the
   whole audit log — the log-lake's job). Fail-safe: an unparseable line never raises.
3. **Storage = the existing `events` hypertable**, `source = "config"`. No schema change, no migration.
   Reuses the per-`(device, "config")` cursor, the `ON CONFLICT` dedup, and the `events` retention store.
4. **v1 scope = timeline + Overview card + report section + drift alerts** (the full package).
5. **Best-effort drift attribution (the core).** Each change is classified by **channel** from `<SRC_PATH>`:
   - `api` — `/api/…` (programmatic: OPNGMS, *or* a WebGUI MVC page, *or* another API client).
   - `gui` — a non-`/api` `.php` WebGUI page (legacy GUI form).
   - `system` — a script under `/usr/local/opnsense/scripts/…`, **or** the local `(<user>)` form (no IP) —
     console / cron / firmware tooling.
   - `unknown` — none of the above (fail-safe default).

   **Drift = channel ∈ {gui, system}** — a **direct on-box change** (OPNGMS never uses these channels; it
   only ever writes via `/api`). Drift events get `severity = "medium"` and raise a **deduped alert**.
   `api`-channel changes get `severity = "info"`, **no alert**, but the actor `user@ip` is always surfaced
   so the operator can eyeball OPNGMS's management IP vs a stray API client.
6. **Honest v1 limitation (this is "best-effort", as chosen):** the `api` channel cannot, by path alone,
   separate OPNGMS from a human using a modern WebGUI MVC page (both hit `/api/…`). The strong drift signal
   is the **gui/system channel** + the **actor IP**. Per-device "management source IP/CIDR" attribution
   (mark `api`-from-our-IP as OPNGMS) is a **documented follow-up**, not in v1 (YAGNI).
7. **Out of scope:** field-level config diffs (the audit log gives the changed *page/endpoint*, not a diff;
   a real diff would need config.xml snapshots — the Revert pipeline's job); correlating box changes to
   OPNGMS's own ledger entries; the management-IP attribution refinement (follow-up).

## Architecture

```
 worker per-device cycle ──▶ ingest_events(... "config") ──▶ client.get_config_changes(since)
                                       │                        POST diagnostics/log/core/audit
                                       ▼                        (matrix-resolved, paged)
   parse_config_changes(rows): match "changed configuration" -> attribute channel/area/actor
                                       │  (other lines dropped, fail-safe)
                                       ▼
   events hypertable (source="config")  ──┬──▶  GET /events?source=config&device_id=  (Config-changes tab)
                                          ├──▶  Overview card aggregate ("Direct config changes 24h")
                                          ├──▶  report "config_changes" section (period rollup)
                                          └──▶  raise Alert on a new DRIFT (gui/system) change
```

## Component 1 — Connector capability + parser (backend)

- **`app/connectors/opnsense/profiles.py`** — add a `config_changes` capability:
  `_POST("diagnostics/log/core/audit", {"current":1,"rowCount":MAX_QUERY_ROWS,"searchPhrase":""})` →
  `parsers.parse_config_changes`. (Same endpoint as `auth_failures`; the two parsers filter different
  line families, so the duplicate POST per poll is acceptable and consistent with one capability per
  source.)
- **`app/connectors/opnsense/client.py`** — `get_config_changes(since=None)` via
  `_capability("config_changes")` (mirror `get_auth_failures`/`get_service_events`).
- **`app/connectors/opnsense/parsers.py`** — `parse_config_changes(data) -> list[dict]`:
  - Iterate `_rows(data, "rows")`; keep only `process_name == "audit"` rows whose `line` matches the
    compiled config-change regex (a single fail-safe pattern):

    ```
    user (?:\((?P<luser>[^)]+)\)|(?P<ruser>[^@\s]+)@(?P<ip>\d{1,3}(?:\.\d{1,3}){3}))
    \s+changed configuration to\s+(?P<backup>\S+)\s+in\s+(?P<path>\S+)
    ```
  - Derive: `actor = luser or ruser`; `actor_ip = ip or ""`; `channel` from `path`
    (`/api/`→api, `*.php` under `/usr/local/opnsense/scripts/`→system, other `*.php`→gui, local-form→system,
    else unknown); `area` = first informative path segment (skip `api`); `change_ref` = path with any
    trailing `/<uuid>` stripped (`/api/monit/settings/delTest` not `…/<uuid>`); `backup_file` = basename of
    `<backup>`.
  - `drift = channel in {"gui","system"}`; `severity = "medium" if drift else "info"`.
  - Emit `{time, category=area, src_ip=actor_ip, name=actor, severity, action=channel,
    event_key=event_key(ts, backup_file), attributes={actor, actor_ip, channel, area, change_ref,
    backup_file, message: line[:500]}}`. Non-matching rows are skipped (fail-safe).
  - The regex/channel rules are a **RUNTIME-VERIFY** starter set (grounded on real api + system samples;
    the gui sample is synthesized) — verified/tuned against the box, same posture as the service classifier.

## Component 2 — Ingest + drift alerts (backend)

- **`app/services/ingest.py`** — add `"config"` to `SOURCES`; a `_fetch` branch
  `client.get_config_changes(since)`. `_normalize` already carries the generic fields. Collect the newly
  inserted **drift** rows (via the existing `RETURNING`-only-inserted path) and pass them to alerting —
  generalize the current service-only `collect` to also gather config-drift rows (e.g. collect when
  `source in {"service","config"}`, then route by source).
- **`app/services/alerting.py`** — `raise_drift_alerts(session, device, new_drift_rows)`: for each NEW
  drift change raise a deduped `Alert(type="config_drift", label=f"Direct config change on {device.name} by
  {actor}")`. Best-effort; an alert failure never aborts ingest (mirror `raise_service_alerts`). Only
  `severity=="medium"` (drift) rows are passed in — `api` changes never alert.

## Component 3 — Frontend (device tab + Overview card)

- **Device page** — a new **"Config changes"** tab: a paginated timeline calling the existing
  `GET /events?source=config&device_id=<id>` (keyset `after`, from/to). Columns: **time · area · actor ·
  IP · channel · change** (`change_ref`), with a **"Direct"** badge on drift (gui/system) rows. Mirror the
  Reliability tab component (`frontend/src/reliability/`).
- **Overview** — a fleet **summary card "Direct config changes (24h)"**: count of drift events in the last
  24h (and/or devices with a recent direct change). Reuse the reliability/perimeter Overview-card pattern
  (`GET /events/top` or a small aggregate filtered to `source=config` + drift).
- **i18n** — all new strings added to `en.ts` first, then mirrored across the 12 sibling locales
  (`it es fr de pt nl ru ar zh zhTW ja`), compiler-enforced parity. `npm run build` is the gate.

## Component 4 — Report section (backend + PDF)

- **`app/services/reporting/sections.py`** — add `"config_changes"` to `SECTION_KEYS` and a
  `BUILTIN_DEFAULTS` entry (default **on**, alongside the other value sections).
- **Report builder + PDF** — a `config_changes` section that, for the report period and the enabled-device
  set, rolls up config-change events (totals, **direct/drift** count highlighted, a notable-changes list:
  time · device · actor · area · channel) and renders a section in the per-client PDF, following the
  `reliability`/`failed_logins` section precedent. Honors the standard toggle precedence
  (`BUILTIN_DEFAULTS < tenant < per-device/schedule`) and the report↔retention range guard.

## Data model

No schema change, no migration: `source="config"` rows in the existing `events` hypertable; alerts in the
existing `alert` table (new `type="config_drift"`); report toggle in the existing section model.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Audit-log source unavailable on a device | `ingest_events` skips a failing source without blocking others |
| Non-config / unparseable audit line | skipped by the parser (only config-change events stored) |
| Local-form change with no IP | `actor_ip=""`, `channel=system` (still recorded) |
| Drift alert raise fails | caught; ingest still commits the events |
| Duplicate change across polls | `event_key(ts, backup_file)` + `ON CONFLICT DO NOTHING` (idempotent) |
| Report section enabled but no changes in range | renders an empty/"no changes" section |

## Security

- Read-only audit-log pull through the existing SSRF-guarded connector; no new outbound path, no secrets.
- The timeline/aggregate reuse the existing `DEVICE_VIEW`-gated, tenant-scoped (RLS) events API — no new
  authz surface. Alerts and the report section are tenant-scoped like all others.
- The parser stores a bounded `{actor, actor_ip, channel, area, change_ref, backup_file, message[:500]}`
  per matched line — no credential material is parsed or logged. The actor IP is operational metadata
  already present in the box log, not a secret.

## Testing

- **Parser (pure):** the three real/realistic line forms → expected `{channel, area, actor, actor_ip,
  drift, event_key}` — `api` remote (`root@ip`, `/api/firewall/filter/addRule` → firewall/api/info),
  `system` local (`(root)`, script `.php` → system/medium/drift), `gui` (`root@ip`, `/firewall_rules.php`
  → firewall/gui/medium/drift); noise rows (configd.py, failed-login, garbage) → dropped; uuid stripping;
  `event_key` stability/dedup on `backup_file`.
- **Ingest:** `"config"` source wired; cursor advances; `ON CONFLICT` dedup across two polls; a failing
  source doesn't block others; only drift rows are collected for alerting.
- **Alerts:** a new drift change raises one deduped alert; an `api` change raises none; a repeat doesn't;
  an alert failure doesn't abort ingest.
- **Connector:** `get_config_changes` posts the audit endpoint, parser applied (`respx.mock`).
- **API reuse:** `GET /events?source=config&device_id=` returns the timeline, keyset-paginated.
- **Report:** the `config_changes` section renders with changes and degrades to empty; toggle precedence.
- **Frontend:** the Config-changes tab renders the timeline + Direct badge (mock the events API); the
  Overview card renders; `npm run build` green (i18n parity).
- **Live verify (box):** generate api changes (apply via the connector) + observe `channel=api`; confirm
  the parser classifies real rows; (if feasible) a console/GUI change → `channel∈{system,gui}` + alert.

## Build phases (informs the plan)

- **PR1 — Backend ingest + drift alerts:** connector capability + `parse_config_changes` + `SOURCES`
  wiring + ingest-time drift alerts + tests.
- **PR2 — Frontend:** device "Config changes" tab (existing events API) + Overview card + i18n (12) + build.
- **PR3 — Report section:** `config_changes` section (builder + PDF + toggle) + tests.
- **PR4 — Docs + live-verify + version:** README + Wiki + CHANGELOG; live-verify on the box; tag.

## Out of scope / future

- Field-level config diffs (no diff in the audit log; that's the Revert/config.xml-snapshot path).
- **Management-IP attribution** — mark `api`-from-our-management-IP as "OPNGMS" vs a stray API client
  (the stronger drift signal for the `api` channel). Deferred follow-up (needs a per-device/tenant mgmt
  source-IP/CIDR setting, optionally auto-learned).
- Correlating box changes with OPNGMS's own audit ledger (the v0.10.0 viewer).

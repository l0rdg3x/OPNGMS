# Security / Perimeter — design

**Status:** approved (brainstorm 2026-06-14) · **Type:** feature (monitoring / threat visibility)

## Problem & goal

Operators have no view of who is attacking a managed firewall at the perimeter: **failed login
attempts** to the box (GUI/SSH) and **traffic blocked by the firewall**. Both carry an attacker source
IP that the existing GeoIP layer can resolve to a country. Surface these in the UI (summary on the
per-tenant Overview, with a dedicated detail page) and in the per-tenant PDF report, reusing the
existing GeoIP attacker-country resolution.

## Feasibility — verified on the real box (192.168.1.82, OPNsense 26.1.x, read-only)

The OPNsense diagnostics-log API uses **POST** (a GET returns `[]` for every scope):

- **Failed logins** → `POST /api/diagnostics/log/core/audit` with `{"current":1,"rowCount":N,"searchPhrase":"…"}`.
  Returns rows `{timestamp, severity, process_name, pid, line}`. Authentication events have
  `process_name="audit"` (confirmed: session-timeout / "Successful" / "authenticat" lines carry a
  **username + source IP**). The box used for verification is a LAN box with **no real failed logins**
  (0 brute-force), so the exact *failure* line wording is verified against OPNsense source + the
  confirmed success/session format during the build (see Caveats).
- **Firewall blocks** → `GET/POST /api/diagnostics/firewall/log`. **Structured** rows:
  `action` (block/pass), `src` (attacker IP), `dst`, `srcport`, `dstport`, `interface`, `protoname`,
  `__timestamp__`, `__digest__`, `label`. The verification box had 213 real blocks (scans on
  137/138/1900/…). We ingest `action=block` only.
- **Bonus (out of scope here):** the same audit log also carries `user X@<ip> changed configuration …`
  lines — the data source for the separate queued "Audit delle modifiche sul box" milestone.

## Storage decision (kept light — operator chose "don't make it too heavy")

Firewall blocks are **high volume** (thousands/day per device). We therefore do **NOT** store either
signal as per-packet/per-attempt rows in the `events` hypertable. Instead, a dedicated **bounded
rollup** table aggregates by attacker IP:

```
perimeter_attacker
  tenant_id     uuid     (RLS, fail-closed policy like every tenant table)
  device_id     uuid
  kind          text     'login_failed' | 'firewall_block'
  src_ip        text     attacker source IP
  count         bigint   total observations
  first_seen    timestamptz
  last_seen     timestamptz
  detail        jsonb    kind-specific: {top_ports, top_protos, interfaces} | {usernames, last_username}
  PRIMARY KEY (device_id, kind, src_ip)
```

- Bounded to **distinct attacker IPs per device per kind** (hundreds, not millions of packets).
- The existing `events` table and the existing IDS-based **attacker-countries** widget are **untouched**
  — no coupling, no pollution of attacker-countries with auth/firewall IPs.
- **Country is resolved at query time** via the existing `geoip_provider` (consistent with
  attacker-countries; keeps the rollup current as the GeoIP DB updates).
- A retention sweep (cron) prunes rows whose `last_seen` is older than a window (default 30 days),
  keeping the table small. (Tunable later; not a runtime setting in v1.)

## Architecture

### Ingest (backend) — reuse the pipeline machinery, upsert aggregates
The existing event ingest (`app/services/ingest.py`, cron `ingest_device_events`) is source-pluggable
with a per-`(device, source)` cursor. We add a **parallel perimeter-ingest** that reuses that
machinery (the SSRF-guarded `OpnsenseClient`, a `(device, kind)` cursor) but whose store step
**UPSERTs into `perimeter_attacker`** instead of inserting `Event` rows:

- New `OpnsenseClient` capabilities in `profiles.py` (version-aware, like `ids_alerts`):
  - `auth_failures`: `POST diagnostics/log/core/audit` (paged, `searchPhrase`) → a parser that keeps
    only authentication-failure lines and extracts `(time, username, src_ip)`.
  - `firewall_blocks`: `firewall/log` (paged) → keep `action=block`, extract
    `(time, src_ip, dst, dstport, interface, protoname, digest)`.
- A parser module `app/connectors/opnsense/parsers` gains `parse_auth_failures` + `parse_firewall_blocks`.
- An aggregator `app/services/perimeter.py`: given a device + the fetched rows since the cursor,
  groups by `src_ip`, and UPSERTs the rollup (count += n, last_seen = max, first_seen = min, merge
  `detail`). Advances the cursor by reusing `IngestCursor` (keyed by `(device, source)`) with `source`
  set to the `kind` value — i.e. `source ∈ {'login_failed','firewall_block'}`, the same strings as the
  rollup's `kind` column, so the two stay consistent.
- Wired into the worker: extend the existing per-device events cron (or a sibling cron) to also run
  perimeter ingest. Errors in one signal never block the other or the metrics/events pipeline
  (same resilience contract as `ingest_events`).

### Aggregation API (backend)
Add to `app/services/reporting/aggregation.py` (`ReportAggregator`) + a router (extend
`app/api/monitoring.py` or a new `app/api/perimeter.py`), all tenant-scoped + RBAC-gated like
`attacker-countries`:

- `GET …/perimeter/summary?kind=&window=` → top-N attacker IPs for a kind: `{src_ip, country,
  count, last_seen, label}` where `label` = last username (login) / top port (firewall). Backs the
  Overview cards.
- `GET …/perimeter/attackers?kind=&window=&page=` → paginated full list (per-IP) for the detail page.
- Country resolved via `geoip_provider` (reuse the attacker-countries resolution helper).

### UI (frontend)
- **Overview (per-tenant):** two summary cards mirroring `AttackerCountriesCard` — `FailedLoginsCard`
  + `FirewallBlocksCard` (top N attacker IPs: IP + country + count + username/port), each linking to
  the detail page.
- **Dedicated page** `/perimeter` (new nav item, label "Perimeter" — distinct from the existing
  account-security `/security/*` pages): a time-window selector + tables (failed logins by IP / by
  username; firewall blocks by IP / by port), with country + counts + last-seen.
- **i18n:** new keys in `en.ts` mirrored into all 12 locales (the UI is now fully translated; keep it
  that way). Build gate enforces parity.

### Reports — two new sections, toggled **per device**
The perimeter signals are per-device, so (operator's call) their report toggle is **per device**, not
the tenant-wide section switch the other sections use:

- A per-device setting `report_perimeter` (JSONB on `devices`, default
  `{"failed_logins": true, "firewall_blocks": true}`) gates whether that device's perimeter data is
  included. Configured on the **device detail page** (two switches). `build_context` already supports a
  per-device scope and iterates devices, so the report's perimeter sections aggregate **only the
  devices whose toggle is on** (a simple join filter on the `perimeter_attacker` rollup).
- The sections render in the PDF (`report.html.j2`) in the attacker-countries style (top attacker IPs +
  countries), fed by `ReportAggregator` methods that take the enabled-device set. Because the toggle is
  per-device, these two sections are **not** added to the tenant-level `sections.py BUILTIN_DEFAULTS`
  switch; the section appears when ≥1 device has it enabled (default on for all devices).

## Decomposition — ~4 PRs (each shippable, green CI + review)
1. **Backend ingest** — the `perimeter_attacker` model + migration, the two `OpnsenseClient`
   capabilities + parsers, `app/services/perimeter.py` aggregator + cursor, worker wiring, retention
   sweep. Tests (fixture log/firewall payloads → rollup upserts; resilience; RLS).
2. **Backend aggregation API** — `ReportAggregator` methods + the `/perimeter/*` endpoints +
   GeoIP enrichment + tests (RBAC, tenant isolation, ranking).
3. **Frontend** — Overview cards + the `/perimeter` page + nav + i18n (12 locales) + tests + the
   `npm run build` gate.
4. **Reports** — the per-device `report_perimeter` toggle end-to-end: the `devices.report_perimeter`
   JSONB column + migration, the two switches on the device detail page (+ i18n), the two report
   sections filtering by the enabled-device set + PDF rendering, and tests; refresh the demo report.

## Caveats
- **Exact failed-login line wording**: this box has no real failed logins. The parser is written
  against OPNsense's known authentication-log patterns + the confirmed success/session format, and is
  verified/adjusted during PR1 against the real box (a single deliberate failed GUI/SSH login is a
  benign, reversible action — to be done with the operator's go-ahead) or against OPNsense source.
  The parser fails safe: an unrecognized line is skipped, never crashes ingest.
- Firewall-block volume is bounded by the rollup; a noisy device can still produce many distinct
  attacker IPs — the retention sweep + the per-poll row cap keep it bounded.

## Invariants
- All box calls go through the SSRF-guarded `OpnsenseClient` (no new unguarded outbound).
- `perimeter_attacker` is tenant-scoped with a fail-closed RLS policy; the worker writes as the owner,
  the API reads as `opngms_app` with the per-request tenant context.
- No secret is logged/returned. Attacker IPs are not secrets; usernames seen in failed logins are
  shown to the tenant's own operators only (RLS-scoped).

## Out of scope (future)
- Config-change audit ("Audit delle modifiche sul box") — separate queued milestone, same audit-log
  source.
- Feeding firewall/auth attacker IPs into the existing attacker-countries map (kept decoupled in v1).
- Per-packet/per-attempt forensic timeline (the rollup keeps counts + first/last-seen, not every row).
- Alerting/thresholds on perimeter spikes (could be a follow-up).

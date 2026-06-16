# Config-audit Management-IP Attribution (auto-learned) — Design Spec

**Date:** 2026-06-16
**Status:** Approved (design); writing the implementation plan next.
**Covers:** The documented follow-up to the config-change audit milestone (v0.16.0): close the **`api`
channel ambiguity** by **auto-learning** OPNGMS's own management source IP and flagging `api` changes that
come from a *different* IP (a human on a modern WebGUI MVC page, or another API client) as **drift**.

## Goal

Today a config change classified as channel **`api`** (request path `/api/…`) cannot be separated into
"OPNGMS's own push" vs "a human using a modern WebGUI MVC page" vs "another API client" — all three hit
`/api/…`. So a direct-but-API change is invisible (no drift alert). This milestone teaches OPNGMS its own
**management source IP** (auto-learned, no manual config) and reclassifies `api` changes by actor IP:
OPNGMS's IP → expected; any other IP → **drift**.

## Verified facts (real box 192.168.1.82)

- The config-audit line carries the actor IP: `user root@192.168.6.100 changed configuration to … in
  /api/…`; the parser already extracts `src_ip` (the `@ip`) + `action` (the channel). For a local/script
  change `src_ip` is empty.
- OPNGMS records its **own** applied changes in the `config_changes` ledger (`device_id`, `status="applied"`,
  `applied_at`) — set by the apply pipeline (`config_push.py`). This is the **ground truth** for "this
  change was OPNGMS's".
- The events `_normalize` carries the parsed event's `action`/`severity`/`attributes` through to the stored
  `events` row, so mutating a parsed config-audit event **before** `_store_source` persists the refinement.
- The Overview card aggregates by `action` (`/events/top?field=action`); the report rolls up by channel
  (`action`). So new `action` values flow through both with no API change.

## Design

### 1. Model — `Device.mgmt_source_ip` (auto-learned)

A new nullable `Device.mgmt_source_ip: str | None` (forward-only **migration 0042** + the model field). Null
until learned; once set, it is the IP the box sees OPNGMS connecting from.

### 2. Auto-learn (at config-audit ingest, correlated with the OPNGMS ledger)

A new `_attribute_mgmt_ip(session, device, events)` runs over the config-audit source's **parsed** events in
`ingest_events`, **before** they are stored:

- **Learn:** among the `action == "api"` events that carry a `src_ip`, find the one whose `time` is closest
  to an OPNGMS-applied change for this device (`SELECT applied_at FROM config_changes WHERE device_id=:did
  AND status='applied' AND applied_at BETWEEN time-W AND time+W`, W = 3 min). If exactly such a correlated
  event exists, set `device.mgmt_source_ip = that event's src_ip` (update if it changed — handles OPNGMS's
  egress IP changing). OPNGMS serializes applies per device and dominates the management plane, so the
  correlated IP is OPNGMS's; a rare coincident external change self-corrects on the next apply. Conservative:
  if the closest-correlated api events disagree on IP within the same batch, skip learning (ambiguous).

### 3. Reclassify (once `mgmt_source_ip` is known)

For each `action == "api"` event in the batch, when `device.mgmt_source_ip` is set:

- `src_ip == mgmt_source_ip` → `action = "opngms"`, `severity = "info"` (expected, no alert);
  `attributes.origin = "opngms"`.
- `src_ip != mgmt_source_ip` (and src_ip non-empty) → `action = "api_external"`, `severity = "medium"`
  (drift); `attributes.origin = "api_external"`, `attributes.drift = True`.

**Until `mgmt_source_ip` is null**, `api` events are left exactly as today (`action="api"`, `severity="info"`)
— **behaviour-preserving, zero false positives** before learning.

### 4. Alert

`api_external` events are `severity="medium"`, so they are collected by the existing
`RETURNING`-inserted-rows path and `raise_config_audit_alerts` (which already alerts on `severity=="medium"`)
opens a deduped `config_audit` alert. `gui`/`system` drift alerts are unchanged. No new alert code.

### 5. Frontend + report (surface the new channels)

- The parser/ingest now emit two new `action` values: **`opngms`** and **`api_external`** (in addition to
  `api`/`gui`/`system`). The frontend `channelLabel` + `channels` i18n map gain `opngms` ("OPNGMS") and
  `api_external` ("External API"); the **Direct** badge predicate extends to `api_external` (it is drift).
  The report i18n gains `config_channel_opngms` + `config_channel_api_external`, and the report's
  drift/direct count includes `api_external`.
- All new strings across the 12 frontend locales + the 12 report locales (compiler-/test-enforced parity).

## Invariants

- The **pure parser** (`parse_config_changes`) is unchanged (channel from path only). The IP-based
  refinement happens at **ingest time**, where the persisted `Device` (with `mgmt_source_ip`) and the
  session are available.
- RLS/secrets/SSRF untouched. `mgmt_source_ip` is operational metadata (an IP the box already logged), not
  a secret. The correlation query is tenant/device-scoped (the device is loaded under the caller's context).
- Behaviour-preserving until the IP is learned.

## Testing

- **Learn:** seed an OPNGMS `config_changes` (applied) + a config-audit `api` event with an IP near its
  `applied_at` → `device.mgmt_source_ip` is set to that IP; a non-correlated api event does not teach;
  ambiguous batch (two IPs) does not teach; a changed IP updates.
- **Reclassify:** with `mgmt_source_ip` set, an `api` event from that IP → `opngms`/info (no alert); from a
  different IP → `api_external`/medium (raises one deduped alert); `gui`/`system` unchanged; with
  `mgmt_source_ip` null, an `api` event stays `api`/info (no alert).
- **Ingest integration:** the refinement runs in `ingest_events` for the `config_audit` source only; other
  sources untouched; cursor/dedup/resilience unchanged.
- **Frontend:** the tab/card render the `opngms`/`api_external` labels + the Direct badge on `api_external`;
  `npm run build` green (i18n parity). **Report:** the by-channel section shows the new channels; the
  direct count includes `api_external`.
- **Live-verify (box):** apply a change via OPNGMS → after the next ingest, `mgmt_source_ip` is learned and
  that change shows as `opngms`; (if feasible) a WebGUI/other-IP api change shows as `api_external` + alerts.

## Build phases (informs the plan)

- **PR1 — backend:** migration 0042 (`Device.mgmt_source_ip`) + `_attribute_mgmt_ip` (learn + reclassify) in
  `ingest.py` + tests. (Alerts work via the existing path — no new alert code.)
- **PR2 — frontend + report i18n:** `opngms`/`api_external` channel labels + Direct-badge extension across
  the frontend (12 locales) and the report (12 locales) + the report direct-count.
- **PR3 — docs + live-verify + version:** CHANGELOG/README/Wiki; live-verify on the box; tag.

## Out of scope

- A manual override of the management IP (auto-learn only, per the user's choice). A per-tenant/per-device
  manual setting could be a later addition if auto-learn proves insufficient.
- SSH/console origin sub-attribution (already `system`).

# OPNsense Connector — Real-Hardware Verification & Fix — Design Spec

**Date:** 2026-06-10
**Status:** Approved (design)
**Scope:** Read/telemetry path of the OPNsense connector. Write path (`apply_alias` / config push, 4D-b) is out of scope (separate spec).

## Goal

Make `OpnsenseClient` work against real OPNsense hardware by replacing the unverified ("TO VERIFY") endpoints and field mappings with the shapes verified against a live OPNsense **26.1.9** ("Witty Woodpecker"), while keeping every public normalized-output contract unchanged so downstream services and their tests are unaffected.

## Architecture

Preserve the connector's public method contracts (the normalized dicts consumed by `monitoring.py`, `ingest.py`, `capability.py`). Change only which endpoints are called and how raw JSON is parsed. Extract the raw-JSON→normalized-dict mapping into **pure functions** (`parsers.py`) tested against **real JSON fixtures** captured from the live box. The client methods become thin: call the guarded HTTP boundary, hand the raw JSON to a parser, return the normalized result.

## Tech Stack

Python 3.14, httpx (async), the existing SSRF-guarded `_request` boundary, pytest + respx for HTTP-level tests, plain pytest for pure-parser tests.

---

## Background — verification results (live box 26.1.9)

Verified on 2026-06-10 against `https://192.168.1.82` (self-signed cert → `verify_tls=False`; API key file outside the repo). The SSRF guard already allows RFC1918, so a LAN firewall onboards correctly.

| Method (contract) | Old endpoint | Verdict | Correct endpoint / shape |
|---|---|---|---|
| `test_connection` / `get_firmware_status` | `GET core/firmware/status` | ⚠️ version mislocated | version is at `product.product_version` (top-level `product_version` absent) |
| `get_plugin_info` | `GET core/firmware/info` | 🟡 over-reports | top-level `product_version` ok; reads `package` (929) — should read `plugin` (102) |
| `get_config_backup` | `GET core/backup/download/this` | ✅ correct | raw `<?xml…><opnsense>` (not wrapped), octet-stream |
| `get_gateways` | `GET routes/gateway/status` | ✅ correct | `{items:[{name,address,status,loss,delay,stddev,status_translated}]}` |
| `get_system_info` | `GET diagnostics/system/systemInformation` | ❌ wrong | returns `{name,versions,updates}`; real metrics split across 4 endpoints (below) |
| `get_interfaces` | `GET diagnostics/interface/getInterfaceStatistics` | ❌ wrong shape | use `diagnostics/traffic/interface` |
| `get_vpn_status` | `GET wireguard/service/show` | ❌ wrong key | real envelope key is `rows`, not `tunnels` |
| `get_ids_alerts` | `GET ids/service/queryAlerts` | ❌ wrong method | must be **POST**; GET returns a bare `[]` that crashes `.get()` |
| `get_dns_events` | `GET unbound/diagnostics/queries` | ❌ 404 | use `GET unbound/overview/searchQueries` |

Cross-checked against the official OPNsense API docs and Context7 (`/opnsense/docs`).

### Captured sample values (anchors for the parsers)

- `system/systemResources` → `{"memory":{"total":"8462950400","used":755341425,...}}` → mem_pct = used/total×100 ≈ 8.9%.
- `system/systemDisk` → `{"devices":[{"used_pct":1,"mountpoint":"/"},...]}` → disk_pct = `used_pct` of mountpoint `/`.
- `system/systemTime` → `{"uptime":"00:11:14","loadavg":"0.12, 0.20, 0.12",...}` → uptime parsed to seconds; load1m = first loadavg term.
- `cpu_usage/getCPUType` → `["Intel(R) Core(TM) i5-4210Y CPU @ 1.50GHz (2 cores, 4 threads)"]` → cores = 2.
- `diagnostics/traffic/interface` → `{"interfaces":{"wan":{"name":"WAN","link state":"2","bytes received":"394684","bytes transmitted":"5116981",...},"opt1":{"name":"LAN","link state":"0",...}}}`. **link state** is the FreeBSD enum (0=unknown, 1=down, **2=up**), not a boolean: WAN (with traffic) = "2", LAN (no cable) = "0".
- `core/firmware/status` → `{"product":{"product_version":"26.1.9", "CORE_VERSION":..., ...},"status":"none","status_msg":"..."}`.
- `core/firmware/info` → top-level `"product_version":"26.1.9"`, arrays `plugin` (len 102) / `package` (len 929), each element with `name` and `installed` ("1"/"0" string).
- `ids/service/queryAlerts` **POST** `{current,rowCount,searchPhrase}` → `{"filters":[],"rows":[],"origin":"eve.json","rowCount":0,"total":0,"current":1}` (empty on a box without IDS data).
- `unbound/overview/searchQueries` GET → `{"total":0,"rowCount":0,"current":1,"rows":[]}` (empty until Unbound query logging is enabled).
- `wireguard/service/show` GET → `{"total":0,"rowCount":0,"current":1,"rows":[]}` (empty until a tunnel is configured).

---

## Component design

### New: `backend/app/connectors/opnsense/parsers.py` (pure functions)

No HTTP, no I/O. Each function tolerates missing keys (defaults, never raises on shape).

- `parse_firmware_version(status_or_info: dict) -> str` — returns `product.product_version` (firmware/status) or top-level `product_version` (firmware/info), else `""`.
- `parse_plugins(info: dict) -> dict` — `{"product_version": str, "plugins": list[str]}`; plugins from the `plugin` array where `installed` ∈ {"1","true","True"}.
- `parse_system_info(resources: dict, disk: dict, time: dict, cputype: list) -> dict` — `{"cpu_pct","mem_pct","disk_pct","uptime_seconds"}`:
  - `mem_pct = round(used/total*100, 1)` from `resources["memory"]` (0.0 if total falsy).
  - `disk_pct = float(used_pct)` of the device whose `mountpoint == "/"` (0.0 if absent).
  - `uptime_seconds = parse_uptime(time["uptime"])` — handles `"HH:MM:SS"` and `"N day(s), HH:MM:SS"`.
  - `load1m = float(time["loadavg"].split(",")[0])`; `cores = parse_cores(cputype)` (regex `(\d+)\s+cores`, default 1); `cpu_pct = min(100.0, round(load1m/cores*100, 1))`.
- `parse_uptime(s: str) -> int` and `parse_cores(cputype: list) -> int` — helpers.
- `parse_interfaces(traffic: dict) -> list[dict]` — for each value in `traffic["interfaces"]`: `{"name": v["name"], "up": str(v.get("link state")) == "2", "bytes_in": _num(v.get("bytes received")), "bytes_out": _num(v.get("bytes transmitted"))}`.
- `parse_gateways(data: dict) -> list[dict]` — unchanged logic (moved here): from `data["items"]`, `up = status not in {"down","force_down"}`, `_num` over `delay`/`loss` (handles `" ms"`, `" %"`, `"~"`→0).
- `parse_vpn(data: dict) -> list[dict]` — from `data["rows"]`: `{"name": row.get("name") or row.get("instance") or row.get("if",""), "up": <truthy>}`, where `<truthy>` is the boolean of `row["connected"]` (values `True`/`"1"`/`"yes"`/`"true"`); if `connected` is absent, fall back to "has a non-empty, non-zero `latest-handshake`". Exact row keys are locked by the live populated fixture (test tunnel).
- `parse_ids_rows(data) -> list[dict]` and `parse_dns_rows(data) -> list[dict]` — accept either a dict (read `rows`/`alerts`/`queries`) **or** a bare list (the empty-GET edge); return the existing normalized event dicts. Field extraction stays defensive (eve.json: `alert.signature`, `src_ip`, `dest_ip`, `alert.severity`, `alert.action`, `timestamp`, `alert_id`/`_id`; DNS: `client`/`client_ip`, `domain`/`query`/`name`, `action`, `time`/`timestamp`). `event_key` logic unchanged (stable id else content hash).

`_num`, `_parse_ts`, `_event_key` move to `parsers.py` (currently static methods on the client) so parsers are self-contained; the client imports them.

### Changed: `backend/app/connectors/opnsense/client.py`

Methods become thin wrappers; contracts unchanged:

- `get_system_info()` — `await` the 4 GETs (`system/systemResources`, `system/systemDisk`, `system/systemTime`, `cpu_usage/getCPUType`), pass to `parse_system_info`.
- `get_interfaces()` — `GET diagnostics/traffic/interface` → `parse_interfaces`.
- `get_gateways()` — `GET routes/gateway/status` → `parse_gateways` (unchanged behavior).
- `get_vpn_status()` — `GET wireguard/service/show` → `parse_vpn`.
- `get_ids_alerts(since)` — **POST** `ids/service/queryAlerts` with `{"current":1,"rowCount":MAX,"searchPhrase":""}` → `parse_ids_rows`.
- `get_dns_events(since)` — `GET unbound/overview/searchQueries?current=1&rowCount=<MAX_EVENTS>` → `parse_dns_rows`.
- `get_plugin_info()` — `GET core/firmware/info` → `parse_plugins`.
- `get_firmware_status()` — returns `{"product_version": parse_firmware_version(data)}` (normalized so callers' `.get("product_version")` works); `test_connection()` uses the same.

`_request` already supports `method="POST", json=...` — no boundary change.

### Changed: `backend/app/services/monitoring.py`

`collect_and_store` already reads `fw.get("product_version")`; it now works because `get_firmware_status()` surfaces the normalized field. No structural change beyond confirming the call.

---

## Error handling

- Parsers never raise on unexpected/missing shape (defaults). HTTP/transport errors keep mapping to `OpnsenseError` subclasses in `_request` (unchanged).
- IDS/DNS endpoints on a box without the feature (404 or empty) degrade to **0 events**: `ingest._ingest_source` already catches `OpnsenseError` per source; the new list-vs-dict guard removes the `AttributeError` that the old GET-returns-`[]` path produced.
- `get_system_info` issues 4 GETs; a transport failure on any propagates as `OpnsenseError` → device marked `unverified` (consistent with current behavior). Missing JSON keys do **not** fail the poll (parser defaults).

---

## Testing strategy

- **Fixtures** `backend/tests/fixtures/opnsense/*.json` — real responses captured from the box: `system_resources`, `system_disk`, `system_time`, `cpu_type`, `traffic_interface`, `firmware_status`, `firmware_info`, `gateway_status`, `wireguard_show` (+ populated), `ids_query_alerts_empty` (+ populated), `unbound_search_queries_empty` (+ populated). Obvious secrets are not fixtured (config.xml is excluded; telemetry fixtures may retain the box's LAN IPs/MACs — acceptable for a lab fixture).
- **`test_opnsense_parsers.py`** — pure unit tests: feed each fixture to its parser, assert the normalized output (mem/disk/cpu/uptime numbers, interface up-flags from link state, gateway up logic, IDS list-vs-dict guard, DNS/VPN row mapping).
- **`test_connector_*` (extended)** — `respx` mocks return the fixtures; assert each method hits the correct path + HTTP method and returns the normalized contract. Regression cases: IDS GET-returns-list does not crash; DNS 404 degrades to `[]`/0 events; firmware version surfaces from `product.product_version`.
- **`scripts/verify_opnsense_live.py`** — captures fixtures and re-verifies against real hardware; **not run in CI** (no hardware). Reads credentials from an env/file path, never prints them.
- Existing `monitoring`/`ingest`/`poller` tests inject fake clients returning the same contracts → unaffected.

## Live capture & box mutation plan (consented — test box)

During implementation, using the granted access to the test box: (1) run the live verify script to snapshot all read-only fixtures; (2) enable Unbound query logging and issue a few lookups to capture a populated `searchQueries` row; (3) create a throwaway WireGuard server/peer to capture a populated `service/show` row, then delete it; (4) best-effort enable IDS to capture a populated `queryAlerts` row (a synthetic eve.json-shaped row, derived from the documented sample, is the fallback if no live alert is generated). Revert all box changes afterwards.

## Out of scope

- `apply_alias` / write path / config push (4D-b) — separate spec.
- CPU% via the `cpu_usage/stream` SSE endpoint — loadavg-derived chosen instead.
- TimescaleDB continuous aggregates / compression — incompatible with RLS (prior decision).

## File structure summary

- **Create:** `backend/app/connectors/opnsense/parsers.py`, `backend/tests/fixtures/opnsense/*.json`, `backend/tests/test_opnsense_parsers.py`, `scripts/verify_opnsense_live.py`.
- **Modify:** `backend/app/connectors/opnsense/client.py` (6 methods thinned + firmware normalization), `backend/app/services/monitoring.py` (confirm version read), the existing connector test module (respx fixture-based cases).
- **Unchanged contracts:** `monitoring.py`, `ingest.py`, `capability.py` consumers.

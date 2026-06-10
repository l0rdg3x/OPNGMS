# OPNsense Connector — Real-Hardware Verification & Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `OpnsenseClient`'s unverified endpoints and field mappings with the shapes verified against a live OPNsense 26.1.9, keeping every public normalized-output contract unchanged.

**Architecture:** Extract raw-JSON→normalized-dict mapping into pure functions in `backend/app/connectors/opnsense/parsers.py`, tested against real captured JSON fixtures. The client methods become thin (call the SSRF-guarded boundary → parser → return). Downstream consumers (`monitoring.py`, `ingest.py`, `capability.py`) are untouched because the normalized contracts are preserved.

**Tech Stack:** Python 3.14, httpx (async), pytest + respx (HTTP-level tests), plain pytest (pure-parser tests). Tests are async with `@respx.mock`; the project uses pytest-asyncio auto mode (no per-test marker — match existing connector tests).

**Spec:** `docs/superpowers/specs/2026-06-10-opnsense-connector-realhw-verification-design.md`

**Branch:** `feat/opnsense-connector-realhw` (already created; spec already committed there).

**Verified facts (anchors):** OPNsense 26.1.9. `link state` is the FreeBSD enum (0=unknown,1=down,**2=up**). IDS `queryAlerts` is **POST** (GET returns a bare `[]`). DNS source is `unbound/overview/searchQueries` (old `unbound/diagnostics/queries` → 404). System metrics split across `systemResources`/`systemDisk`/`systemTime`/`cpu_usage/getCPUType`. Firmware version lives at `product.product_version` in `firmware/status`.

---

## File Structure

- **Create:**
  - `backend/app/connectors/opnsense/parsers.py` — pure normalizers (one responsibility: shape mapping).
  - `backend/tests/fixtures/opnsense/*.json` — real captured response slices.
  - `backend/tests/opn_fixtures.py` — fixture loader helper.
  - `backend/tests/test_opnsense_parsers.py` — pure-parser unit tests.
  - `scripts/verify_opnsense_live.py` — read-only live capture/verify (not in CI).
- **Modify:**
  - `backend/app/connectors/opnsense/client.py` — thin methods + firmware normalization; remove the `_num`/`_parse_ts`/`_event_key` static methods (moved to parsers).
  - `backend/tests/test_connector_system_info.py`, `test_connector_network.py`, `test_connector_ids.py`, `test_connector_dns.py`, `test_connector_plugin_info.py` — update to verified endpoints/shapes.
- **Unchanged (re-run to confirm green):** `backend/app/services/monitoring.py` (the firmware fix is in `get_firmware_status`), `test_opnsense_client.py`, `test_connector_tls_pinning.py`, `test_onboarding.py`, `test_connector_config.py`, all integration/poller/ingest tests (they inject fake clients).

---

## Task 1: Real-shape test fixtures + loader

**Files:**
- Create: `backend/tests/fixtures/opnsense/system_resources.json`, `system_disk.json`, `system_time.json`, `cpu_type.json`, `traffic_interface.json`, `gateway_status.json`, `firmware_status.json`, `firmware_info.json`, `wireguard_show_empty.json`, `wireguard_show.json`, `ids_query_alerts_empty.json`, `ids_query_alerts.json`, `unbound_search_queries_empty.json`, `unbound_search_queries.json`
- Create: `backend/tests/opn_fixtures.py`

- [ ] **Step 1: Create the fixture JSON files** (trimmed-real content captured from OPNsense 26.1.9; synthetic-but-documented for the empty-on-fresh-install populated cases — marked below)

`system_resources.json`:
```json
{"memory":{"total":"8462950400","total_frmt":"8070","used":755341425,"used_frmt":"720","arc":"227278664"}}
```

`system_disk.json`:
```json
{"devices":[{"device":"zroot/ROOT/default","type":"zfs","blocks":"223G","used":"1.2G","available":"222G","used_pct":1,"mountpoint":"/"},{"device":"/dev/gpt/efiboot0","type":"msdosfs","used_pct":1,"mountpoint":"/boot/efi"},{"device":"zroot/tmp","type":"zfs","used_pct":0,"mountpoint":"/tmp"}]}
```

`system_time.json`:
```json
{"uptime":"00:11:14","datetime":"Wed Jun 10 20:49:46 UTC 2026","boottime":"Wed Jun 10 20:38:32 UTC 2026","config":"Wed Jun 10 20:43:04 UTC 2026","loadavg":"0.12, 0.20, 0.12"}
```

`cpu_type.json`:
```json
["Intel(R) Core(TM) i5-4210Y CPU @ 1.50GHz (2 cores, 4 threads)"]
```

`traffic_interface.json`:
```json
{"interfaces":{"opt1":{"device":"igb0","name":"LAN","link state":"0","flags":"8843","line rate":"10000000 bit/s","bytes received":"0","bytes transmitted":"0"},"wan":{"device":"igb3","name":"WAN","link state":"2","flags":"8843","line rate":"1000000000 bit/s","bytes received":"394684","bytes transmitted":"5116981"}}}
```

`gateway_status.json`:
```json
{"items":[{"name":"WAN_DHCP6","address":"fe80::d620:ff:feb1:d727","status":"none","loss":"~","delay":"~","stddev":"~","monitor":"~","status_translated":"Online"},{"name":"WAN_DHCP","address":"192.168.1.1","status":"none","loss":"~","delay":"~","stddev":"~","monitor":"~","status_translated":"Online"}],"status":"ok"}
```

`firmware_status.json` (version under `product`, no top-level `product_version` — the real 26.1.9 shape):
```json
{"product":{"product_version":"26.1.9","product_name":"OPNsense","product_nickname":"Witty Woodpecker","CORE_VERSION":"26.1.9"},"status_msg":"Firmware status requires to check for update first.","status":"none"}
```

`firmware_info.json` (top-level `product_version`; `plugin` vs `package` arrays):
```json
{"product_id":"opnsense","product_version":"26.1.9","plugin":[{"name":"os-wireguard","installed":"1"},{"name":"os-theme-cicada","installed":"0"}],"package":[{"name":"base","installed":"1"}],"product":{"product_version":"26.1.9"}}
```

`wireguard_show_empty.json`:
```json
{"total":0,"rowCount":0,"current":1,"rows":[]}
```

`wireguard_show.json` (synthetic; documented `rows` shape — to be validated/replaced by live capture, see Task 6 note):
```json
{"total":1,"rowCount":1,"current":1,"rows":[{"name":"wg0 (peer1)","connected":true,"latest-handshake":"1718050000"}]}
```

`ids_query_alerts_empty.json`:
```json
{"filters":[],"rows":[],"origin":"eve.json","rowCount":0,"total":0,"current":1}
```

`ids_query_alerts.json` (synthetic, eve.json-shaped from the documented sample):
```json
{"rows":[{"timestamp":"2026-06-10T20:45:00+00:00","src_ip":"192.168.1.50","dest_ip":"8.8.8.8","alert":{"signature":"ET SCAN Nmap","severity":2,"action":"allowed"},"alert_id":"a1"}],"origin":"eve.json","rowCount":1,"total":1,"current":1}
```

`unbound_search_queries_empty.json`:
```json
{"total":0,"rowCount":0,"current":1,"rows":[]}
```

`unbound_search_queries.json` (synthetic; documented `rows` shape):
```json
{"rows":[{"time":"2026-06-10T20:45:00+00:00","client":"192.168.1.50","domain":"example.com","action":"allowed"}],"total":1,"rowCount":1,"current":1}
```

- [ ] **Step 2: Create the loader** `backend/tests/opn_fixtures.py`

```python
"""Loader for the real-shape OPNsense response fixtures."""
import json
from pathlib import Path

_DIR = Path(__file__).parent / "fixtures" / "opnsense"


def load(name: str):
    """Return the parsed JSON of fixtures/opnsense/<name>."""
    return json.loads((_DIR / name).read_text())
```

- [ ] **Step 3: Smoke-test that fixtures load** — add `backend/tests/test_opnsense_parsers.py` with only this test for now:

```python
from tests.opn_fixtures import load


def test_fixtures_load():
    assert load("system_resources.json")["memory"]["total"] == "8462950400"
    assert load("traffic_interface.json")["interfaces"]["wan"]["link state"] == "2"
    assert load("firmware_status.json")["product"]["product_version"] == "26.1.9"
```

- [ ] **Step 4: Run it**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add backend/tests/fixtures/opnsense backend/tests/opn_fixtures.py backend/tests/test_opnsense_parsers.py
git commit -m "test(opnsense): real-shape response fixtures + loader

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: parsers.py foundation + system metrics

**Files:**
- Create: `backend/app/connectors/opnsense/parsers.py`
- Test: `backend/tests/test_opnsense_parsers.py` (extend)
- Modify: `backend/app/connectors/opnsense/client.py` (rewire `get_system_info`)
- Modify: `backend/tests/test_connector_system_info.py` (rewrite to the 4 real endpoints)

- [ ] **Step 1: Write failing parser tests** — append to `backend/tests/test_opnsense_parsers.py`:

```python
from app.connectors.opnsense import parsers


def test_num_handles_units_and_tilde():
    assert parsers.num("12.3 ms") == 12.3
    assert parsers.num("0.0 %") == 0.0
    assert parsers.num("~") == 0.0
    assert parsers.num(5) == 5.0
    assert parsers.num(None) == 0.0


def test_parse_uptime():
    assert parsers.parse_uptime("00:11:14") == 674
    assert parsers.parse_uptime("2 days, 03:00:01") == 2 * 86400 + 3 * 3600 + 1
    assert parsers.parse_uptime("") == 0


def test_parse_cores():
    assert parsers.parse_cores(["Intel(R) ... (2 cores, 4 threads)"]) == 2
    assert parsers.parse_cores([]) == 1


def test_parse_system_info_against_real_fixtures():
    info = parsers.parse_system_info(
        load("system_resources.json"),
        load("system_disk.json"),
        load("system_time.json"),
        load("cpu_type.json"),
    )
    assert info["mem_pct"] == 8.9        # 755341425 / 8462950400 * 100
    assert info["disk_pct"] == 1.0       # used_pct of mountpoint "/"
    assert info["uptime_seconds"] == 674  # 00:11:14
    assert info["cpu_pct"] == 6.0        # load1m 0.12 / 2 cores * 100
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: FAIL (ModuleNotFoundError: app.connectors.opnsense.parsers / AttributeError).

- [ ] **Step 3: Create `backend/app/connectors/opnsense/parsers.py`** with the shared helpers + system parsers:

```python
"""Pure parsers: raw OPNsense JSON -> normalized dicts. No HTTP, no I/O.

Every function tolerates missing/unexpected keys (safe defaults) and never raises on
shape. The shapes were verified against a live OPNsense 26.1.9 (see the connector design
spec). Keeping these pure makes them testable against captured fixtures without HTTP.
"""
import hashlib
import re
from datetime import datetime, timezone


def num(v) -> float:
    """First float in a string like '12.3 ms' / '0.0 %' / '~' / a number; 0.0 if none."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
    return float(m.group()) if m else 0.0


def parse_ts(value) -> datetime:
    """Always tz-aware (naive -> UTC; unparsable -> now UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def event_key(ts: datetime, *parts) -> str:
    """Discriminating hash of event content (used when no stable source id is present)."""
    h = hashlib.sha1("|".join([ts.isoformat(), *[str(p) for p in parts]]).encode())
    return h.hexdigest()


def parse_uptime(s) -> int:
    """'HH:MM:SS' or 'N day(s), HH:MM:SS' -> seconds. 0 on unparsable."""
    s = str(s or "")
    days_match = re.search(r"(\d+)\s+day", s)
    days = int(days_match.group(1)) if days_match else 0
    hms = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if not hms:
        return days * 86400
    h, mi, sec = (int(x) for x in hms.groups())
    return days * 86400 + h * 3600 + mi * 60 + sec


def parse_cores(cputype) -> int:
    """['... (2 cores, 4 threads)'] -> 2. Default 1."""
    text = cputype[0] if isinstance(cputype, list) and cputype else str(cputype or "")
    m = re.search(r"(\d+)\s+cores?", str(text))
    return int(m.group(1)) if m else 1


def parse_system_info(resources: dict, disk: dict, time: dict, cputype) -> dict:
    """CPU/mem/disk/uptime from the four diagnostics endpoints. CPU% is loadavg-derived."""
    mem = (resources or {}).get("memory", {}) or {}
    total = num(mem.get("total"))
    used = num(mem.get("used"))
    mem_pct = round(used / total * 100, 1) if total else 0.0

    disk_pct = 0.0
    for d in (disk or {}).get("devices", []) or []:
        if d.get("mountpoint") == "/":
            disk_pct = num(d.get("used_pct"))
            break

    uptime_seconds = parse_uptime((time or {}).get("uptime"))
    load_terms = str((time or {}).get("loadavg", "")).split(",")
    load1m = num(load_terms[0]) if load_terms else 0.0
    cores = parse_cores(cputype)
    cpu_pct = min(100.0, round(load1m / cores * 100, 1)) if cores else 0.0

    return {
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "disk_pct": disk_pct,
        "uptime_seconds": uptime_seconds,
    }
```

- [ ] **Step 4: Run parser tests to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS.

- [ ] **Step 5: Rewire the client** — in `backend/app/connectors/opnsense/client.py`, add at the top of the file (after the existing imports):

```python
from app.connectors.opnsense import parsers
```

Replace the `get_system_info` method body with:

```python
    async def get_system_info(self) -> dict:
        """CPU/mem/disk/uptime, aggregated from four diagnostics endpoints (26.1.9)."""
        resources = await self._get("diagnostics/system/systemResources")
        disk = await self._get("diagnostics/system/systemDisk")
        time = await self._get("diagnostics/system/systemTime")
        cputype = await self._get("diagnostics/cpu_usage/getCPUType")
        return parsers.parse_system_info(resources, disk, time, cputype)
```

- [ ] **Step 6: Rewrite the client test** — replace the entire contents of `backend/tests/test_connector_system_info.py`:

```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_system_info_aggregates_real_endpoints():
    respx.get(f"{BASE}/api/diagnostics/system/systemResources").mock(
        return_value=httpx.Response(200, json=load("system_resources.json")))
    respx.get(f"{BASE}/api/diagnostics/system/systemDisk").mock(
        return_value=httpx.Response(200, json=load("system_disk.json")))
    respx.get(f"{BASE}/api/diagnostics/system/systemTime").mock(
        return_value=httpx.Response(200, json=load("system_time.json")))
    respx.get(f"{BASE}/api/diagnostics/cpu_usage/getCPUType").mock(
        return_value=httpx.Response(200, json=load("cpu_type.json")))

    info = await OpnsenseClient(BASE, "k", "s").get_system_info()
    assert info["mem_pct"] == 8.9
    assert info["disk_pct"] == 1.0
    assert info["uptime_seconds"] == 674
    assert info["cpu_pct"] == 6.0
```

- [ ] **Step 7: Run both tests to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py tests/test_connector_system_info.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/app/connectors/opnsense/client.py backend/tests/test_opnsense_parsers.py backend/tests/test_connector_system_info.py
git commit -m "fix(opnsense): real system metrics via 4 diagnostics endpoints + pure parsers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Network parsers (interfaces, gateways, VPN)

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py` (add `parse_interfaces`, `parse_gateways`, `parse_vpn`, `_truthy`)
- Test: `backend/tests/test_opnsense_parsers.py` (extend)
- Modify: `backend/app/connectors/opnsense/client.py` (rewire `get_interfaces`, `get_gateways`, `get_vpn_status`)
- Modify: `backend/tests/test_connector_network.py` (rewrite to real shapes)

- [ ] **Step 1: Write failing parser tests** — append to `backend/tests/test_opnsense_parsers.py`:

```python
def test_parse_interfaces_link_state_up():
    out = parsers.parse_interfaces(load("traffic_interface.json"))
    by = {i["name"]: i for i in out}
    assert by["WAN"]["up"] is True        # link state "2"
    assert by["WAN"]["bytes_in"] == 394684.0
    assert by["WAN"]["bytes_out"] == 5116981.0
    assert by["LAN"]["up"] is False       # link state "0" (unknown / no carrier)


def test_parse_gateways_tilde_and_status():
    out = parsers.parse_gateways(load("gateway_status.json"))
    by = {g["name"]: g for g in out}
    assert by["WAN_DHCP"]["up"] is True   # status "none" is up
    assert by["WAN_DHCP"]["rtt_ms"] == 0.0   # "~" -> 0.0
    assert by["WAN_DHCP"]["loss_pct"] == 0.0
    # a down gateway:
    down = parsers.parse_gateways({"items": [
        {"name": "G2", "status": "down", "delay": "12.3 ms", "loss": "100.0 %"}]})
    assert down[0]["up"] is False and down[0]["rtt_ms"] == 12.3 and down[0]["loss_pct"] == 100.0


def test_parse_vpn_reads_rows():
    assert parsers.parse_vpn(load("wireguard_show_empty.json")) == []
    out = parsers.parse_vpn(load("wireguard_show.json"))
    assert out == [{"name": "wg0 (peer1)", "up": True}]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: FAIL (AttributeError: module has no attribute 'parse_interfaces').

- [ ] **Step 3: Add the parsers** — append to `backend/app/connectors/opnsense/parsers.py`:

```python
def parse_interfaces(traffic: dict) -> list[dict]:
    """diagnostics/traffic/interface -> [{name, up, bytes_in, bytes_out}].

    `link state` is the FreeBSD enum (0=unknown, 1=down, 2=up); only "2" is up.
    """
    out = []
    for v in (traffic or {}).get("interfaces", {}).values():
        out.append({
            "name": v.get("name", ""),
            "up": str(v.get("link state")) == "2",
            "bytes_in": num(v.get("bytes received")),
            "bytes_out": num(v.get("bytes transmitted")),
        })
    return out


def parse_gateways(data: dict) -> list[dict]:
    """routes/gateway/status -> [{name, up, rtt_ms, loss_pct}]. '~'/units handled by num()."""
    out = []
    for g in (data or {}).get("items", []) or []:
        status = str(g.get("status", "")).lower()
        out.append({
            "name": g.get("name", ""),
            "up": status not in ("down", "force_down"),
            "rtt_ms": num(g.get("delay")),
            "loss_pct": num(g.get("loss")),
        })
    return out


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def parse_vpn(data: dict) -> list[dict]:
    """wireguard/service/show -> [{name, up}]. Envelope key is `rows` (not `tunnels`)."""
    out = []
    for row in (data or {}).get("rows", []) or []:
        name = row.get("name") or row.get("instance") or row.get("if", "")
        if "connected" in row:
            up = _truthy(row.get("connected"))
        else:
            hs = str(row.get("latest-handshake", "")).strip()
            up = bool(hs) and hs != "0"
        out.append({"name": name, "up": up})
    return out
```

- [ ] **Step 4: Run parser tests to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS.

- [ ] **Step 5: Rewire the client** — in `backend/app/connectors/opnsense/client.py`, replace the bodies of `get_interfaces`, `get_gateways`, `get_vpn_status` with:

```python
    async def get_interfaces(self) -> list[dict]:
        """Per-interface bytes + up flag (diagnostics/traffic/interface)."""
        data = await self._get("diagnostics/traffic/interface")
        return parsers.parse_interfaces(data)

    async def get_gateways(self) -> list[dict]:
        """Gateway RTT / packet-loss / up (routes/gateway/status)."""
        data = await self._get("routes/gateway/status")
        return parsers.parse_gateways(data)

    async def get_vpn_status(self) -> list[dict]:
        """WireGuard tunnel/peer status (wireguard/service/show; envelope key `rows`)."""
        data = await self._get("wireguard/service/show")
        return parsers.parse_vpn(data)
```

- [ ] **Step 6: Rewrite the client test** — replace the entire contents of `backend/tests/test_connector_network.py`:

```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_interfaces_traffic_endpoint():
    respx.get(f"{BASE}/api/diagnostics/traffic/interface").mock(
        return_value=httpx.Response(200, json=load("traffic_interface.json")))
    ifs = await OpnsenseClient(BASE, "k", "s").get_interfaces()
    by = {i["name"]: i for i in ifs}
    assert by["WAN"] == {"name": "WAN", "up": True, "bytes_in": 394684.0, "bytes_out": 5116981.0}
    assert by["LAN"]["up"] is False


@respx.mock
async def test_get_gateways():
    respx.get(f"{BASE}/api/routes/gateway/status").mock(
        return_value=httpx.Response(200, json=load("gateway_status.json")))
    gws = await OpnsenseClient(BASE, "k", "s").get_gateways()
    by = {g["name"]: g for g in gws}
    assert by["WAN_DHCP"]["up"] is True and by["WAN_DHCP"]["rtt_ms"] == 0.0


@respx.mock
async def test_get_vpn_status_reads_rows():
    respx.get(f"{BASE}/api/wireguard/service/show").mock(
        return_value=httpx.Response(200, json=load("wireguard_show.json")))
    vpn = await OpnsenseClient(BASE, "k", "s").get_vpn_status()
    assert vpn == [{"name": "wg0 (peer1)", "up": True}]
```

- [ ] **Step 7: Run to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py tests/test_connector_network.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/app/connectors/opnsense/client.py backend/tests/test_opnsense_parsers.py backend/tests/test_connector_network.py
git commit -m "fix(opnsense): interfaces via traffic/interface (link-state up), vpn via rows

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Firmware parsers (version + plugins)

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py` (add `parse_firmware_version`, `parse_plugins`)
- Test: `backend/tests/test_opnsense_parsers.py` (extend)
- Modify: `backend/app/connectors/opnsense/client.py` (rewire `get_firmware_status`, `get_plugin_info`, `test_connection`)
- Modify: `backend/tests/test_connector_plugin_info.py` (use the `plugin` array)

- [ ] **Step 1: Write failing parser tests** — append to `backend/tests/test_opnsense_parsers.py`:

```python
def test_parse_firmware_version_from_product_subtree():
    # firmware/status: version is under product.product_version (no top-level field)
    assert parsers.parse_firmware_version(load("firmware_status.json")) == "26.1.9"
    # firmware/info: top-level product_version present
    assert parsers.parse_firmware_version(load("firmware_info.json")) == "26.1.9"
    assert parsers.parse_firmware_version({}) == ""


def test_parse_plugins_reads_plugin_array_not_package():
    out = parsers.parse_plugins(load("firmware_info.json"))
    assert out["product_version"] == "26.1.9"
    assert out["plugins"] == ["os-wireguard"]   # installed "1" from the `plugin` array
    assert "base" not in out["plugins"]          # `package` array is ignored
    assert "os-theme-cicada" not in out["plugins"]  # installed "0"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: FAIL (AttributeError: parse_firmware_version).

- [ ] **Step 3: Add the parsers** — append to `backend/app/connectors/opnsense/parsers.py`:

```python
def parse_firmware_version(data: dict) -> str:
    """Version from top-level `product_version` (firmware/info) or `product.product_version`
    (firmware/status). Empty string if absent."""
    data = data or {}
    v = data.get("product_version")
    if not v and isinstance(data.get("product"), dict):
        v = data["product"].get("product_version")
    return v or ""


def parse_plugins(info: dict) -> dict:
    """firmware/info -> {product_version, plugins}. Reads the `plugin` array (OPNsense
    plugins) and keeps only installed ones — NOT the much larger `package` array."""
    info = info or {}
    plugins = [
        p.get("name", "")
        for p in info.get("plugin", []) or []
        if str(p.get("installed", "")) in ("1", "true", "True") and p.get("name")
    ]
    return {"product_version": parse_firmware_version(info), "plugins": plugins}
```

- [ ] **Step 4: Run parser tests to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS.

- [ ] **Step 5: Rewire the client** — in `backend/app/connectors/opnsense/client.py`, replace `get_firmware_status`, `get_plugin_info`, and `test_connection` with:

```python
    async def get_firmware_status(self) -> dict:
        """Connection test + firmware version. Normalizes the version to the top level so
        callers (monitoring) read `.get("product_version")` regardless of the raw nesting."""
        data = await self._get("core/firmware/status")
        return {"product_version": parsers.parse_firmware_version(data)}

    async def get_plugin_info(self) -> dict:
        """Installed plugins + product version, for capability discovery."""
        data = await self._get("core/firmware/info")
        return parsers.parse_plugins(data)
```

and (further down, the existing `test_connection`):

```python
    async def test_connection(self) -> str | None:
        """Verify reachability+credentials; return the firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on problems.
        """
        data = await self.get_firmware_status()
        return data.get("product_version") or None
```

- [ ] **Step 6: Update the plugin-info test** — replace the entire contents of `backend/tests/test_connector_plugin_info.py`:

```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


@respx.mock
async def test_get_plugin_info_reads_plugin_array():
    respx.get(url__regex=r".*/api/core/firmware/info.*").mock(
        return_value=httpx.Response(200, json=load("firmware_info.json")))
    out = await OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False).get_plugin_info()
    assert out["product_version"] == "26.1.9"
    assert "os-wireguard" in out["plugins"]
    assert "base" not in out["plugins"]            # package array ignored
    assert "os-theme-cicada" not in out["plugins"]  # not installed
```

- [ ] **Step 7: Run the affected client tests** (incl. the unchanged ones that must stay green)

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py tests/test_connector_plugin_info.py tests/test_opnsense_client.py tests/test_connector_tls_pinning.py tests/test_onboarding.py -q`
Expected: PASS. (`test_opnsense_client.py` mocks `firmware/status` with a top-level `product_version` → `test_connection` still returns it; pinning/onboarding unaffected.)

- [ ] **Step 8: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/app/connectors/opnsense/client.py backend/tests/test_opnsense_parsers.py backend/tests/test_connector_plugin_info.py
git commit -m "fix(opnsense): normalize firmware version (product.product_version), plugins from plugin array

Fixes monitoring never updating firmware_version (it read the wrong nesting).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Event parsers (IDS POST + DNS overview)

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py` (add `_rows`, `parse_ids_rows`, `parse_dns_rows`)
- Test: `backend/tests/test_opnsense_parsers.py` (extend)
- Modify: `backend/app/connectors/opnsense/client.py` (rewire `get_ids_alerts` → POST, `get_dns_events` → overview; remove the now-unused `_num`/`_parse_ts`/`_event_key` static methods)
- Modify: `backend/tests/test_connector_ids.py` (GET→POST + list edge), `backend/tests/test_connector_dns.py` (new endpoint)

- [ ] **Step 1: Write failing parser tests** — append to `backend/tests/test_opnsense_parsers.py`:

```python
import hashlib


def test_parse_ids_rows_list_and_dict_and_keys():
    # bare list edge (the empty GET used to crash .get()): must not raise
    assert parsers.parse_ids_rows([]) == []
    out = parsers.parse_ids_rows(load("ids_query_alerts.json"))
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "192.168.1.50"
    assert e["dst_ip"] == "8.8.8.8"        # dest_ip
    assert e["name"] == "ET SCAN Nmap"     # alert.signature
    assert e["severity"] == "2"
    assert e["action"] == "allowed"
    assert e["category"] == "alert"
    assert e["event_key"] == "a1"          # stable alert_id
    assert e["time"].tzinfo is not None


def test_parse_ids_rows_hash_fallback_and_variants():
    payload = {"alerts": [{
        "timestamp": "2026-06-09T13:30:00Z", "src_ip": "10.0.0.7", "dst_ip": "8.8.8.8",
        "signature": "ET POLICY DNS", "severity": 3, "action": "blocked"}]}
    e = parsers.parse_ids_rows(payload)[0]
    assert e["name"] == "ET POLICY DNS" and e["dst_ip"] == "8.8.8.8" and e["severity"] == "3"
    expected = hashlib.sha1("|".join([
        e["time"].isoformat(), "10.0.0.7", "8.8.8.8", "ET POLICY DNS", "3"]).encode()).hexdigest()
    assert e["event_key"] == expected


def test_parse_dns_rows():
    assert parsers.parse_dns_rows([]) == []
    out = parsers.parse_dns_rows(load("unbound_search_queries.json"))
    e = out[0]
    assert e["src_ip"] == "192.168.1.50"
    assert e["name"] == "example.com"
    assert e["action"] == "allowed"
    assert e["category"] == "query"
    assert e["dst_ip"] == "" and e["severity"] == ""
    assert e["event_key"] and e["time"].tzinfo is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: FAIL (AttributeError: parse_ids_rows).

- [ ] **Step 3: Add the parsers** — append to `backend/app/connectors/opnsense/parsers.py`:

```python
def _rows(data, *keys) -> list:
    """Rows from a dict (first matching key holding a list) OR a bare list (the empty-GET
    edge that used to crash `.get()`). Anything else -> []."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def parse_ids_rows(data) -> list[dict]:
    """ids/service/queryAlerts (POST) rows -> normalized IDS events (eve.json shape).

    Defensive toward key variants (alert.* nested or flat, dest_ip/dst_ip). event_key is a
    stable source id when present, otherwise a discriminating content hash."""
    out: list[dict] = []
    for r in _rows(data, "rows", "alerts"):
        alert = r.get("alert", {}) if isinstance(r.get("alert"), dict) else {}
        ts = parse_ts(r.get("timestamp"))
        name = alert.get("signature") or r.get("signature") or ""
        src = r.get("src_ip", "")
        dst = r.get("dest_ip", r.get("dst_ip", ""))
        action = alert.get("action", r.get("action", ""))
        severity = str(alert.get("severity", r.get("severity", "")))
        key = r.get("alert_id") or r.get("_id") or event_key(ts, src, dst, name, severity)
        out.append({
            "time": ts, "category": "alert", "src_ip": src, "dst_ip": dst,
            "name": name, "severity": severity, "action": action,
            "event_key": str(key), "attributes": r,
        })
    return out


def parse_dns_rows(data) -> list[dict]:
    """unbound/overview/searchQueries rows -> normalized DNS "visited site" events."""
    out: list[dict] = []
    for r in _rows(data, "rows", "queries"):
        ts = parse_ts(r.get("timestamp", r.get("time")))
        client_ip = r.get("client") or r.get("client_ip") or ""
        domain = r.get("domain") or r.get("query") or r.get("name") or ""
        action = r.get("action", "")
        key = r.get("query_id") or r.get("id") or r.get("_id") or event_key(
            ts, client_ip, domain, action)
        out.append({
            "time": ts, "category": "query", "src_ip": client_ip, "dst_ip": "",
            "name": domain, "severity": "", "action": action,
            "event_key": str(key), "attributes": r,
        })
    return out
```

- [ ] **Step 4: Run parser tests to verify pass**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS.

- [ ] **Step 5: Rewire the client** — in `backend/app/connectors/opnsense/client.py`:

(a) Add a module-level constant near the top (after `MAX_EVENTS` if present, else after the imports):

```python
# Rows requested from the paged IDS/DNS query endpoints (dedup happens downstream).
MAX_QUERY_ROWS = 500
```

(b) Replace `get_ids_alerts` and `get_dns_events` with:

```python
    async def get_ids_alerts(self, since: datetime | None = None) -> list[dict]:
        """Normalized Suricata IDS/IPS alerts. queryAlerts is POST (GET returns a bare []).

        `since` is a hint: fine filtering + dedup happen downstream (cursor + ON CONFLICT)."""
        data = await self._post(
            "ids/service/queryAlerts",
            {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""},
        )
        return parsers.parse_ids_rows(data)

    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        """Normalized DNS queries -> "visited sites" (unbound/overview/searchQueries)."""
        data = await self._get(
            f"unbound/overview/searchQueries?current=1&rowCount={MAX_QUERY_ROWS}"
        )
        return parsers.parse_dns_rows(data)
```

(c) Delete the now-unused static methods `_num`, `_parse_ts`, and `_event_key` from the class (their logic lives in `parsers`). Confirm no remaining references:

Run: `cd backend && rg -n "_num|_parse_ts|_event_key" app/connectors/opnsense/client.py`
Expected: no matches.

- [ ] **Step 6: Update the IDS test** — replace the entire contents of `backend/tests/test_connector_ids.py`:

```python
import hashlib

import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_ids_alerts_uses_post_and_normalizes():
    route = respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=load("ids_query_alerts.json")))
    out = await _client().get_ids_alerts(since=None)
    assert route.called                       # POST, not GET
    e = out[0]
    assert e["src_ip"] == "192.168.1.50" and e["dst_ip"] == "8.8.8.8"
    assert e["name"] == "ET SCAN Nmap" and e["severity"] == "2" and e["action"] == "allowed"
    assert e["event_key"] == "a1" and e["time"].tzinfo is not None


@respx.mock
async def test_get_ids_alerts_bare_list_does_not_crash():
    # Regression: the old GET returned a bare [] and crashed `.get()` with AttributeError.
    respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=[]))
    assert await _client().get_ids_alerts(since=None) == []


@respx.mock
async def test_get_ids_alerts_hash_fallback_is_discriminating():
    payload = {"rows": [
        {"timestamp": "2026-06-09T12:00:00+00:00", "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
         "alert": {"signature": "ET SCAN Nmap", "severity": 2}},
        {"timestamp": "2026-06-09T12:00:00+00:00", "src_ip": "10.0.0.9", "dest_ip": "5.6.7.8",
         "alert": {"signature": "ET SCAN Nmap", "severity": 2}}]}
    respx.post(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload))
    out = await _client().get_ids_alerts(since=None)
    assert len(out) == 2 and out[0]["event_key"] != out[1]["event_key"]
```

- [ ] **Step 7: Update the DNS test** — replace the entire contents of `backend/tests/test_connector_dns.py`:

```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient
from tests.opn_fixtures import load


def _client():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_dns_events_overview_endpoint():
    route = respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json=load("unbound_search_queries.json")))
    out = await _client().get_dns_events(since=None)
    assert route.called
    e = out[0]
    assert e["src_ip"] == "192.168.1.50" and e["name"] == "example.com"
    assert e["action"] == "allowed" and e["category"] == "query"
    assert e["dst_ip"] == "" and e["severity"] == ""
    assert e["event_key"] and e["time"].tzinfo is not None


@respx.mock
async def test_get_dns_events_empty():
    respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json=load("unbound_search_queries_empty.json")))
    assert await _client().get_dns_events() == []
```

- [ ] **Step 8: Run the event tests + ingest (which consumes these contracts)**

Run: `cd backend && python -m pytest tests/test_opnsense_parsers.py tests/test_connector_ids.py tests/test_connector_dns.py tests/test_ingest.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/app/connectors/opnsense/client.py backend/tests/test_opnsense_parsers.py backend/tests/test_connector_ids.py backend/tests/test_connector_dns.py
git commit -m "fix(opnsense): IDS via POST queryAlerts (list-safe), DNS via overview/searchQueries

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Live read-only verify/capture script + full-suite gate

**Files:**
- Create: `scripts/verify_opnsense_live.py`
- (no app changes)

This script is a developer tool to re-capture fixtures and re-verify the connector against
real hardware after an OPNsense upgrade. It is **read-only** and **not run in CI** (no
hardware). It reads credentials from a file path given by an env var — never hardcoded,
never printed.

- [ ] **Step 1: Create `scripts/verify_opnsense_live.py`**

```python
#!/usr/bin/env python3
"""Read-only OPNsense connector verification against real hardware.

Usage:
    OPNSENSE_URL=https://192.168.1.82 \
    OPNSENSE_KEYFILE=~/path/OPNsense.apikey.txt \
    python scripts/verify_opnsense_live.py

The key file has two lines: `key=...` and `secret=...`. Credentials are never printed.
Exercises every read path of OpnsenseClient and prints a PASS/FAIL line per method. With
--dump <dir>, writes the raw JSON responses to <dir> for refreshing test fixtures.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402


def _read_creds(keyfile: str) -> tuple[str, str]:
    key = secret = ""
    for line in Path(keyfile).expanduser().read_text().splitlines():
        if line.startswith("key="):
            key = line[4:].strip()
        elif line.startswith("secret="):
            secret = line[7:].strip()
    if not key or not secret:
        raise SystemExit("key/secret not found in key file")
    return key, secret


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", metavar="DIR", help="write raw JSON responses to DIR")
    args = ap.parse_args()

    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)

    checks = {
        "test_connection": client.test_connection(),
        "get_plugin_info": client.get_plugin_info(),
        "get_system_info": client.get_system_info(),
        "get_interfaces": client.get_interfaces(),
        "get_gateways": client.get_gateways(),
        "get_vpn_status": client.get_vpn_status(),
        "get_ids_alerts": client.get_ids_alerts(),
        "get_dns_events": client.get_dns_events(),
    }
    failures = 0
    results = {}
    for name, coro in checks.items():
        try:
            value = await coro
            results[name] = value
            summary = value if isinstance(value, (str, type(None))) else f"{len(value)} items" \
                if isinstance(value, list) else "ok"
            print(f"PASS  {name:18} -> {summary}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name:18} -> {type(exc).__name__}: {exc}")

    if args.dump:
        d = Path(args.dump)
        d.mkdir(parents=True, exist_ok=True)
        (d / "results.json").write_text(json.dumps(
            {k: (v if not hasattr(v, "isoformat") else str(v)) for k, v in results.items()},
            default=str, indent=2))
        print(f"\nWrote {d/'results.json'}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

- [ ] **Step 2: Run it against the live test box** (read-only)

Run:
```bash
OPNSENSE_URL=https://192.168.1.82 \
OPNSENSE_KEYFILE=/home/l0rdg3x/Scaricati/OPNsense.internal_root_apikey.txt \
python scripts/verify_opnsense_live.py
```
Expected: `test_connection -> 26.1.9`, `get_system_info -> ok`, interfaces/gateways ≥1 item, and `ALL PASS` (IDS/DNS/VPN may be `0 items` on a fresh box — that is a PASS, not a failure).

- [ ] **Step 3: Run the FULL backend suite** (the contract-preservation gate)

Run: `cd backend && python -m pytest -q`
Expected: PASS (full suite). If any integration/poller/monitoring test fails, it indicates a contract drift — fix the connector method to restore the exact normalized dict it returned before, not the test.

- [ ] **Step 4: Run the frontend build + lint sanity** (no frontend change expected; confirm nothing drifted)

Run: `cd frontend && npm run lint`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_opnsense_live.py
git commit -m "tools(opnsense): read-only live connector verification/capture script

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

> **Orchestrator note (not a subagent task):** The `wireguard_show.json`, `ids_query_alerts.json`, and `unbound_search_queries.json` populated fixtures use the documented row shapes (the fresh box returns empty). With the user's standing consent on this test box, after the suite is green the orchestrator may (optionally) enable Unbound query logging, create a throwaway WireGuard tunnel, and enable IDS on the box, re-run `verify_opnsense_live.py --dump` to capture real populated rows, replace the synthetic fixtures if the field names differ, re-run the parser tests, and then revert all box changes. This is a manual, reversible verification — kept out of the automated tasks so subagents never mutate the firewall.

---

## Final verification

- [ ] Full backend suite green: `cd backend && python -m pytest -q`
- [ ] No references to the removed static methods: `cd backend && rg -n "_num\(|_parse_ts\(|_event_key\(" app/connectors/opnsense/client.py` → no matches
- [ ] No references to the old endpoints anywhere in app code: `rg -n "systemInformation|getInterfaceStatistics|unbound/diagnostics/queries" backend/app` → no matches
- [ ] Dispatch a final holistic code review, then use superpowers:finishing-a-development-branch.

---

## Self-Review (author)

**Spec coverage:** system metrics (Task 2), interfaces/gateways/vpn (Task 3), firmware version + plugins + monitoring fix (Task 4), IDS POST + DNS overview + list-safety (Task 5), pure parsers + fixtures + parser/respx tests (Tasks 1–5), live verify script (Task 6), out-of-scope items untouched. All spec sections map to a task.

**Placeholder scan:** every code step shows complete code; every command has expected output; the populated-fixture caveat is concrete (documented shapes + reversible live capture), not a TODO.

**Type consistency:** parser names are stable across tasks (`num`, `parse_ts`, `event_key`, `parse_system_info`, `parse_interfaces`, `parse_gateways`, `parse_vpn`, `parse_firmware_version`, `parse_plugins`, `_rows`, `parse_ids_rows`, `parse_dns_rows`); client methods keep their existing signatures and normalized return contracts; `MAX_QUERY_ROWS` defined once in Task 5.

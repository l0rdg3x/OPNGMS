# OPNsense (edition, version) Capability Resolver — Design Spec

**Date:** 2026-06-11
**Status:** Approved (design)
**Builds on:** the real-hardware connector baseline (`2026-06-10-opnsense-connector-realhw-verification`). The connector is correct for OPNsense 26.1.x (Community); this milestone makes endpoint/parser selection **(edition, version)-aware** so a heterogeneous fleet (Community + Business, many versions, 2026+) is handled by data, not code forks.

## Goal

Detect each device's **edition** (Community / Business) and **version**, and resolve every connector capability (which endpoint + method + parser to use) through a declarative **version matrix**, so supporting a new OPNsense version or a per-edition difference is a data-only change.

## Architecture

A declarative **capability matrix** (`profiles.py`) maps each capability to an ordered list of version/edition-ranged `EndpointSpec`s. A **resolver** (`resolver.py`), given a device's `(edition, version)`, returns the concrete spec per capability. `OpnsenseClient` becomes resolver-driven: each method asks the resolver for its spec, issues the spec's request(s) through the existing SSRF-guarded boundary, and hands the response(s) to the spec's `combine` function (which calls the existing pure parsers). Detection reads `core/firmware/status`; edition+version+series are persisted on the device.

## Tech Stack

Python 3.14, the existing `parsers.py` pure functions (unchanged), the SSRF-guarded `_request` boundary, SQLAlchemy/Alembic (new device columns), pytest + respx.

---

## 1. OPNsense version parsing (hotfix-aware)

OPNsense versions are `YY.M.point[_hotfix]` — major `YY.M` (e.g. `26.1`), point `YY.M.X` (e.g. `26.1.9`), **hotfix** `YY.M.X_Y` (e.g. `24.7.1_1`, `22.4.3_2`). The `_Y` suffix is NOT PEP 440, so `packaging.version` cannot be used. A dedicated parser (added to `parsers.py`, the existing pure-functions module) produces an orderable tuple:

```python
import re

def parse_version(s) -> tuple[int, int, int, int]:
    """OPNsense 'YY.M.point[_hotfix]' -> (year, month, point, hotfix); missing parts -> 0.
    Defensive: non-numeric / unexpected input never raises (best-effort, 0-filled)."""
    base, _, hot = str(s or "").strip().partition("_")
    nums = []
    for part in base.split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    hm = re.match(r"\d+", hot)
    return (nums[0], nums[1], nums[2], int(hm.group()) if hm else 0)

def series_of(s) -> str:
    """'26.1.9_1' -> '26.1' (the YY.M series; hotfix/point ignored)."""
    y, m, _, _ = parse_version(s)
    return f"{y}.{m}"
```

Ordering: `parse_version("26.1.9_1") > parse_version("26.1.9") > parse_version("26.1.8")` (hotfix 1 > 0). All range comparisons use these 4-tuples.

## 2. Detection & device model

`OpnsenseClient.get_device_identity() -> DeviceIdentity{edition, version, series}` reads `core/firmware/status` (`product` dict):
- **edition**: from `product_id` — `opnsense`→`community`, `opnsense-business`→`business`, `opnsense-devel`→`devel`; defensive fallback: if `product_repos`/`product_name` contains "business" → `business`; default `community`. (Verified values on a Community 26.1.9 box: `product_id="opnsense"`, `product_name="OPNsense"`, `product_series="26.1"`, `product_repos="OPNsense (Priority: 11)"`. Business values are **inferred** pending a real Business box — see Scope.)
- **version**: `product.product_version` (e.g. `26.1.9`, possibly `26.1.9_1`).
- **series**: `product.product_series` if present (`26.1`), else `series_of(version)`.

`DeviceIdentity` is a small dataclass in `connectors/opnsense/identity.py`, together with the `get_device_identity(client)` helper (or a `get_device_identity` method on the client that returns it).

**Migration** (Alembic): add to `devices`: `edition` (String, default `""`), `firmware_series` (String, default `""`). `firmware_version` already exists. `monitoring.collect_and_store` and the onboarding probe populate all three.

## 3. The capability matrix — `connectors/opnsense/profiles.py`

```python
from dataclasses import dataclass
from collections.abc import Callable
from app.connectors.opnsense import parsers

@dataclass(frozen=True)
class Request:
    method: str            # "GET" | "POST"
    path: str              # e.g. "diagnostics/traffic/interface" (may include ?query)
    body: dict | None = None
    kind: str = "json"     # "json" | "text"

@dataclass(frozen=True)
class EndpointSpec:
    requests: tuple[Request, ...]      # 1 for most; 4 for system_info
    combine: Callable                  # combine(list_of_decoded_responses) -> normalized result

@dataclass(frozen=True)
class ProfileRule:
    edition: str                       # "community" | "business" | "any"
    min_version: tuple | None          # inclusive lower bound (parse_version tuple) or None
    max_version: tuple | None          # EXCLUSIVE upper bound or None
    spec: EndpointSpec
```

`CAPABILITIES: dict[str, list[ProfileRule]]` — capability name → rules in priority order. Capabilities and their verified-26.1.x default specs:

| capability | request(s) | combine |
|---|---|---|
| `system_info` | GET systemResources, systemDisk, systemTime, cpu_usage/getCPUType | `parse_system_info(r0,r1,r2,r3)` |
| `interfaces` | GET diagnostics/traffic/interface | `parse_interfaces(r0)` |
| `gateways` | GET routes/gateway/status | `parse_gateways(r0)` |
| `vpn_status` | GET wireguard/service/show | `parse_vpn(r0)` |
| `ids_alerts` | POST ids/service/queryAlerts `{current,rowCount,searchPhrase}` | `parse_ids_rows(r0)` |
| `dns_events` | GET unbound/overview/searchQueries?current=1&rowCount=N | `parse_dns_rows(r0)` |
| `firmware_status` | GET core/firmware/status | `parse_firmware_version(r0)` |
| `plugin_info` | GET core/firmware/info | `parse_plugins(r0)` |
| `config_backup` | GET core/backup/download/this (kind=text) | `r0` (raw XML) |

Each capability's default rule is `edition="any", min=None, max=None`. **The matrix is multi-row, not single-row:** `dns_events` additionally carries a documented **legacy** rule for old series — `unbound/diagnostics/queries` (the genuine pre-rename endpoint) with `max_version = (20, 1, 0, 0)` (approximate, commented as best-effort: we have no pre-20.1 hardware; it exists to demonstrate and exercise the matrix). The resolver test proves the legacy rule is selected for an old-series device and the default for current.

## 4. Resolver — `connectors/opnsense/resolver.py`

```python
class CapabilityResolver:
    def __init__(self, edition: str, version: str):
        self.edition = edition or "community"
        self.vtuple = parsers.parse_version(version)
    def resolve(self, capability: str) -> EndpointSpec:
        for rule in CAPABILITIES[capability]:
            if rule.edition not in ("any", self.edition):
                continue
            if rule.min_version and self.vtuple < rule.min_version:
                continue
            if rule.max_version and self.vtuple >= rule.max_version:
                continue
            return rule.spec
        # last resort: the most general (any/None/None) rule — guaranteed present per capability
        return CAPABILITIES[capability][-1].spec
```

Invariant (enforced by a `test_profiles.py` test): every capability's rule list ends with an `edition="any", min=None, max=None` default, so `resolve()` **never raises / never returns None**.

## 5. Client integration

`OpnsenseClient.__init__` accepts optional `edition` / `version`; it builds `self._resolver` lazily: on first capability call, if no resolver, it calls `get_device_identity()` (one firmware/status fetch, memoized) and constructs the resolver. Each capability method collapses to:

```python
async def get_dns_events(self, since=None) -> list[dict]:
    return await self._capability("dns_events")

async def _capability(self, name: str):
    spec = (await self._get_resolver()).resolve(name)
    responses = []
    for req in spec.requests:
        resp = await self._request(req.path, req.method, req.body)
        responses.append(resp.text if req.kind == "text" else self._json(resp))
    return spec.combine(responses)
```

The hard-coded endpoint strings and per-method parser calls move OUT of the methods and INTO the matrix; methods become thin named wrappers over `_capability(...)` (kept for the existing call sites / signatures, e.g. `since` params remain accepted and ignored as today). `test_connection`/identity uses `firmware_status` + `get_device_identity`. Output contracts unchanged → `monitoring.py`/`ingest.py`/`capability.py` consumers unaffected.

## 6. Edition gating & capability inventory

`build_inventory` gains the device `edition` and a per-capability availability check: a capability is "available" for a device if `CAPABILITIES[name]` has a rule matching its `(edition, version)`. Today all capabilities are `edition="any"` (Community-verified), so nothing is gated yet; the **mechanism** exists so Business-only (or version-gated) capabilities added later are surfaced/hidden correctly. The inventory output includes `edition` alongside `opnsense_version`.

## 7. Error handling

- Unknown/empty edition → treated as `community` (most common, most permissive).
- Unparseable version → `parse_version` yields `(0,0,0,0)`; range rules with a `min_version` won't match, so the `any` default is selected. Safe.
- `get_device_identity()` transport/HTTP failure → propagates as `OpnsenseError` (device marked `unverified`), exactly as today.
- The resolver never fails to resolve (guaranteed default rule).

## 8. Testing

- `test_opnsense_version.py` — `parse_version` (incl. `_hotfix`, ordering `26.1.9_1 > 26.1.9`, defensive garbage→`(0,0,0,0)`), `series_of`.
- `test_resolver.py` — **mechanism, with controlled synthetic rules**: edition match, version min/max boundaries (inclusive/exclusive), priority order, fallback to the default, hotfix-boundary selection. Zero dependence on real endpoints.
- `test_profiles.py` — the seeded matrix: every capability ends with an `any/None/None` default (resolver-never-None invariant); a `community 26.1.9` device resolves each capability to the verified 26.1.x spec; an old-series device resolves `dns_events` to the legacy `diagnostics/queries` spec.
- `test_identity.py` / extend connector tests — `get_device_identity` against the real `firmware_status.json` (community) and a synthetic `firmware_status_business.json` → correct `(edition, version, series)`.
- Connector tests parametrized over profiles (respx): the client on a current device hits the current endpoints; on an old-series device, `dns_events` hits `unbound/diagnostics/queries`.
- Migration test for the new `edition`/`firmware_series` columns.
- Fixtures reorganized: keep the verified shapes; add `firmware_status_business.json` (synthetic, pending the real box). The live verify script (`scripts/verify_opnsense_live.py`) is extended to print the detected `(edition, version, series)`.

## 9. Scope boundaries

- **Business profile values are inferred** (`product_id="opnsense-business"` + defensive fallbacks) pending a real Business box (user will provide one ASAP). A synthetic `firmware_status_business.json` + the detection code exist now; verified and corrected when the box arrives — a data-only change.
- The `dns_events` legacy rule's version boundary is **approximate/illustrative** (no pre-rename hardware); it demonstrates the matrix and is exercised by tests, but is not claimed as an exact historical cutover.
- OpenVPN VPN status (only WireGuard today), write path / `apply_alias`, and per-field config-schema versioning remain out of scope (separate milestones).

## 10. File structure

- **Create:** `backend/app/connectors/opnsense/profiles.py` (Request/EndpointSpec/ProfileRule/CAPABILITIES), `backend/app/connectors/opnsense/resolver.py`, `backend/app/connectors/opnsense/identity.py` (DeviceIdentity + get_device_identity helper), an Alembic migration (`edition`, `firmware_series` on `devices`), test fixtures, `test_opnsense_version.py`, `test_resolver.py`, `test_profiles.py`, `test_identity.py`.
- **Modify:** `client.py` (resolver-driven methods + `get_device_identity`), `parsers.py` (add `parse_version`/`series_of`), `models/device.py` (+columns), `services/monitoring.py` + onboarding probe (persist edition/version/series), `services/capability.py` (edition-aware inventory), `scripts/verify_opnsense_live.py` (print identity). The version helpers `parse_version`/`series_of` live in `parsers.py`; `profiles.py` and `resolver.py` import them from there.
- **Unchanged contracts:** the connector's normalized outputs and the `monitoring`/`ingest` consumers.

# OPNGMS — Phase 4 / Milestone 4B: Config Model + Capability Discovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose, per device and read-only, (1) a schema-agnostic navigable JSON tree of the device's latest config with **sensitive values redacted**, and (2) a capability inventory (interfaces, configured sections, OPNsense version, available plugins/modules from a live probe), tenant-scoped and RLS-isolated. Foundation for the firewall-aware UI (4C) and edit/push (4D).

**Architecture:** On-demand from the latest 4A `config_snapshots` row (decrypted + parsed server-side with defusedxml) plus a live plugin/version probe of the device. Pure functions for the model and inventory; thin repository/endpoints reusing the 4A/3C patterns. No new storage.

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy async, Postgres + RLS, `defusedxml`, Fernet, pytest + respx.

---

## Context for the implementer (read first)

Codebase is **English** — write all code/comments/docstrings in English. Phases 1–4A are in `main`.

- **4A reuse**: `app/services/config_diff.py` (defusedxml parse, `_strip_volatile`, `_VOLATILE_TAGS`, the indexed-path scheme `tag[n]`) — the new `config_model` mirrors its parsing/path scheme. `app/models/config_snapshot.py`, `app/repositories/config_snapshot.py` (`list(device_id)` returns newest-first; add a `latest` helper), `app/core/crypto.py` (`decrypt_bytes`), `app/api/config.py` (`_xml(snapshot)` = `gzip.decompress(crypto.decrypt_bytes(content_enc)).decode()`; the tenant-scoped router pattern).
- **Connector**: `app/connectors/opnsense/client.py` — `_get(path) -> dict` (SSRF-guarded). Add `get_plugin_info()` mirroring `get_firmware_status`/`get_system_info`.
- **API/RLS reference (3C/4A)**: `app/api/events.py`, `tests/test_events_rls_api.py` (real `opngms_app` raw-SQL RLS proof), `tests/test_config_api.py`/`test_config_rls_api.py` (4A: seeding encrypted snapshots, no-secret-leak assertions, 401/403).
- **Tests**: `tests/conftest.py` (fixtures `db_engine`, `two_tenants`, `api_client`, `app_role_api_client`). Seed a snapshot with `crypto.encrypt_bytes(gzip.compress(xml.encode()))` into `config_snapshots` (FK device_id → use a seeded device).

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Current suite: **185 tests green**.

**Security guardrails:**
- Parse config XML ONLY via **defusedxml** (never stdlib `ET.fromstring` on device input).
- The model NEVER emits a sensitive value: sensitive leaf → `value: null, sensitive: true`. Redaction
  is **conservative** (when in doubt, redact). Tests must assert no seeded secret string appears in
  any model/API output.
- 4B is read-only. No raw config / secret value is ever returned.

⚠️ **Plugin/version endpoint `core/firmware/info` TO VERIFY** against a real device; mocked with respx.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/services/config_model.py` | `build_tree`, `is_sensitive` (pure) | Create |
| `app/connectors/opnsense/client.py` | `get_plugin_info()` | Modify |
| `app/services/capability_registry.py` | plugin id → capability descriptor | Create |
| `app/services/capability.py` | `build_inventory` (empirical + probe + registry) | Create |
| `app/repositories/config_snapshot.py` | add `latest(device_id)` | Modify |
| `app/schemas/config.py` | `ConfigNode`, `CapabilityInventory`, `Interface`, `Capability` | Modify |
| `app/api/config.py` | `GET /config/model`, `/config/capabilities` | Modify |
| tests | model, connector, capability, API + isolation | Create/Modify |

---

## Task 1: Config model service (`build_tree` + sensitive redaction)

**Files:**
- Create: `app/services/config_model.py`, `tests/test_config_model.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_model.py`:
```python
from app.services.config_model import build_tree, is_sensitive

XML = (
    "<opnsense>"
    "<revision><time>1</time></revision>"
    "<system><hostname>fw1</hostname>"
    "<user><name>root</name><password>topsecret</password></user></system>"
    "<interfaces><wan><if>igb0</if></wan><lan><if>igb1</if></lan></interfaces>"
    "</opnsense>"
)


def test_is_sensitive():
    assert is_sensitive("password") and is_sensitive("api_key") and is_sensitive("PrivateKey")
    assert not is_sensitive("hostname") and not is_sensitive("if")


def test_build_tree_structure_and_order():
    root = build_tree(XML)
    assert root["tag"] == "opnsense"
    # <revision> stripped; order preserved
    top = [c["tag"] for c in root["children"]]
    assert top == ["system", "interfaces"]
    system = root["children"][0]
    hostname = system["children"][0]
    assert hostname["path"] == "opnsense/system/hostname"
    assert hostname["value"] == "fw1"
    assert hostname["sensitive"] is False


def test_sensitive_value_is_redacted_and_never_emitted():
    root = build_tree(XML)
    import json
    blob = json.dumps(root)
    assert "topsecret" not in blob  # secret never appears anywhere
    # locate the password node
    user = root["children"][0]["children"][1]
    pw = [c for c in user["children"] if c["tag"] == "password"][0]
    assert pw["sensitive"] is True and pw["value"] is None


def test_rejects_hostile_xml():
    import pytest

    bomb = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "x"><!ENTITY b "&a;&a;">]><opnsense><x>&b;</x></opnsense>'
    with pytest.raises(Exception):
        build_tree(bomb)
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_config_model.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement the service**

Create `app/services/config_model.py`:
```python
"""Schema-agnostic navigable model of a device config (read-only).

Parses config.xml with defusedxml (XXE/billion-laughs safe), strips the volatile
<revision> node, preserves element order (repeated siblings indexed by position, same
path scheme as config_diff), and emits a JSON tree. Sensitive leaf values (passwords,
keys, secrets...) are REDACTED: the node carries sensitive=True and value=None, and the
secret value never appears in the output. Redaction is conservative (when in doubt, redact).
"""

import xml.etree.ElementTree as ET  # type annotations only — NOT for parsing

from defusedxml.ElementTree import fromstring as _parse_xml

_VOLATILE_TAGS = frozenset({"revision"})

# Conservative denylist of tag substrings that indicate a secret-bearing field.
# A maintained security control: prefer over-redaction (a missed tag would leak a secret).
_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "secret", "psk", "pre-shared-key", "preshared",
    "passphrase", "privatekey", "private_key", "apikey", "api_key",
    "sharedkey", "shared_key", "token", "prv",
)


def is_sensitive(tag: str) -> bool:
    t = tag.lower()
    return any(sub in t for sub in _SENSITIVE_SUBSTRINGS)


def _strip_volatile(root: ET.Element) -> None:
    for child in list(root):
        if child.tag in _VOLATILE_TAGS:
            root.remove(child)


def _node(elem: ET.Element, path: str) -> dict:
    node: dict = {
        "tag": elem.tag,
        "path": path,
        "attributes": dict(elem.attrib),
        "children": [],
        "value": None,
        "sensitive": False,
    }
    children = list(elem)
    if not children:
        if is_sensitive(elem.tag):
            node["sensitive"] = True  # value stays None (redacted)
        else:
            node["value"] = (elem.text or "").strip()
        return node
    tag_total: dict[str, int] = {}
    for child in children:
        tag_total[child.tag] = tag_total.get(child.tag, 0) + 1
    seen: dict[str, int] = {}
    for child in children:
        seen[child.tag] = seen.get(child.tag, 0) + 1
        seg = child.tag if tag_total[child.tag] == 1 else f"{child.tag}[{seen[child.tag]}]"
        node["children"].append(_node(child, f"{path}/{seg}"))
    return node


def build_tree(xml: str) -> dict:
    root = _parse_xml(xml)
    _strip_volatile(root)
    return _node(root, root.tag)
```

- [ ] **Step 4: Run and verify it passes**

Run: `... pytest tests/test_config_model.py -v` → PASS (4/4). Whole suite green.

- [ ] **Step 5: Commit**
```bash
git add app/services/config_model.py tests/test_config_model.py
git commit -m "feat(backend): config_model build_tree with conservative sensitive redaction"
```

---

## Task 2: Connector `get_plugin_info` (live device probe)

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_plugin_info.py`

- [ ] **Step 1: Write the failing respx test**

Create `tests/test_connector_plugin_info.py`:
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_plugin_info_normalizes():
    payload = {
        "product_version": "24.7.2",
        "package": [
            {"name": "os-wireguard", "installed": "1"},
            {"name": "os-firewall", "installed": "1"},
            {"name": "os-not-installed", "installed": "0"},
        ],
    }
    respx.get(url__regex=r".*/api/core/firmware/info.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_plugin_info()
    assert out["product_version"] == "24.7.2"
    assert "os-wireguard" in out["plugins"]
    assert "os-firewall" in out["plugins"]
    assert "os-not-installed" not in out["plugins"]  # only installed
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_connector_plugin_info.py -v` → FAIL.

- [ ] **Step 3: Implement `get_plugin_info`**

In `app/connectors/opnsense/client.py`, add (after `get_firmware_status`):
```python
    async def get_plugin_info(self) -> dict:
        """Installed plugins + product version, for capability discovery.

        NOTE: endpoint `core/firmware/info` and payload shape TO VERIFY against a real
        OPNsense device. Defensive toward key variants.
        """
        data = await self._get("core/firmware/info")
        packages = data.get("package", data.get("plugin", []))
        plugins = [
            p.get("name", "")
            for p in packages
            if str(p.get("installed", "")) in ("1", "true", "True") and p.get("name")
        ]
        version = data.get("product_version") or (data.get("product") or {}).get("product_version", "")
        return {"product_version": version, "plugins": plugins}
```

- [ ] **Step 4: Run and verify it passes**

Run: `... pytest tests/test_connector_plugin_info.py -v` → PASS. Whole suite green.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_plugin_info.py
git commit -m "feat(backend): connector get_plugin_info (installed plugins + version probe)"
```

---

## Task 3: Capability service + registry

**Files:**
- Create: `app/services/capability_registry.py`, `app/services/capability.py`
- Create: `tests/test_capability.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_capability.py`:
```python
from app.services.capability import build_inventory

XML = (
    "<opnsense>"
    "<system><hostname>fw1</hostname></system>"
    "<interfaces>"
    "<wan><if>igb0</if><descr>WAN</descr></wan>"
    "<lan><if>igb1</if><descr>LAN</descr></lan>"
    "</interfaces>"
    "<filter><rule><type>pass</type></rule></filter>"
    "</opnsense>"
)


def test_inventory_empirical_interfaces_and_sections():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": []})
    names = {i["name"] for i in inv["interfaces"]}
    assert names == {"wan", "lan"}
    wan = [i for i in inv["interfaces"] if i["name"] == "wan"][0]
    assert wan["nic"] == "igb0" and wan["description"] == "WAN"
    assert "system" in inv["configured_sections"]
    assert "interfaces" in inv["configured_sections"]
    assert "filter" in inv["configured_sections"]
    assert inv["opnsense_version"] == "24.7"


def test_inventory_maps_known_plugin_capabilities():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": ["os-wireguard"]})
    ids = {c["id"] for c in inv["available_capabilities"]}
    assert "os-wireguard" in ids
    wg = [c for c in inv["available_capabilities"] if c["id"] == "os-wireguard"][0]
    assert wg["label"]  # known plugin has a friendly label


def test_inventory_unknown_plugin_passes_through_generic():
    inv = build_inventory(XML, opnsense_version="24.7", plugin_info={"plugins": ["os-weird-thing"]})
    weird = [c for c in inv["available_capabilities"] if c["id"] == "os-weird-thing"][0]
    assert weird["label"]  # generic descriptor, not crash
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_capability.py -v` → FAIL.

- [ ] **Step 3: Implement the registry**

Create `app/services/capability_registry.py`:
```python
"""Small, extensible registry mapping OPNsense plugin/module ids to capability descriptors.

Seeded with common core/plugins; unknown ids pass through with a generic descriptor.
The exhaustive field-level per-version schema is out of scope (deferred to 4D, device-sourced).
"""

_REGISTRY: dict[str, dict] = {
    "os-wireguard": {"label": "WireGuard VPN", "area": "vpn/wireguard"},
    "os-openvpn": {"label": "OpenVPN", "area": "vpn/openvpn"},
    "os-firewall": {"label": "Firewall rules (API)", "area": "firewall"},
    "os-dhcp": {"label": "DHCP", "area": "services/dhcp"},
    "os-unbound": {"label": "Unbound DNS", "area": "services/unbound"},
    "os-ids": {"label": "Intrusion Detection (Suricata)", "area": "ids"},
    "os-haproxy": {"label": "HAProxy", "area": "services/haproxy"},
}


def describe(plugin_id: str) -> dict:
    base = _REGISTRY.get(plugin_id)
    if base is None:
        return {"id": plugin_id, "label": plugin_id, "area": ""}  # generic pass-through
    return {"id": plugin_id, **base}
```

- [ ] **Step 4: Implement the capability service**

Create `app/services/capability.py`:
```python
"""Per-device capability inventory: empirical (from config) + live probe + registry."""

import xml.etree.ElementTree as ET  # type annotations only

from defusedxml.ElementTree import fromstring as _parse_xml

from app.services.capability_registry import describe


def _interfaces(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    ifaces = root.find("interfaces")
    if ifaces is None:
        return out
    for el in list(ifaces):
        out.append({
            "name": el.tag,
            "nic": (el.findtext("if") or "").strip(),
            "description": (el.findtext("descr") or "").strip(),
        })
    return out


def build_inventory(xml: str, opnsense_version: str, plugin_info: dict) -> dict:
    root = _parse_xml(xml)
    configured_sections = [el.tag for el in list(root) if el.tag != "revision"]
    available = [describe(pid) for pid in plugin_info.get("plugins", [])]
    return {
        "opnsense_version": opnsense_version,
        "interfaces": _interfaces(root),
        "configured_sections": configured_sections,
        "available_capabilities": available,
    }
```

- [ ] **Step 5: Run and verify it passes**

Run: `... pytest tests/test_capability.py -v` → PASS (3/3). Whole suite green.

- [ ] **Step 6: Commit**
```bash
git add app/services/capability_registry.py app/services/capability.py tests/test_capability.py
git commit -m "feat(backend): capability inventory (empirical interfaces/sections + plugin registry)"
```

---

## Task 4: API (`/config/model` + `/config/capabilities`) + isolation

**Files:**
- Modify: `app/repositories/config_snapshot.py`, `app/schemas/config.py`, `app/api/config.py`
- Create: `tests/test_config_model_api.py`, `tests/test_config_model_rls_api.py`

- [ ] **Step 1: Add a `latest` repository helper**

In `app/repositories/config_snapshot.py`, add:
```python
    async def latest(self, device_id: uuid.UUID) -> "ConfigSnapshot | None":
        rows = await self.list(device_id)
        return rows[0] if rows else None
```

- [ ] **Step 2: Add schemas**

In `app/schemas/config.py`, add:
```python
class Interface(BaseModel):
    name: str
    nic: str
    description: str


class Capability(BaseModel):
    id: str
    label: str
    area: str


class CapabilityInventory(BaseModel):
    opnsense_version: str
    interfaces: list[Interface]
    configured_sections: list[str]
    available_capabilities: list[Capability]
```
(The config model tree is returned as a free-form dict — `response_model=dict` — since it is recursive/schema-agnostic.)

- [ ] **Step 3: Write the failing API tests**

Create `tests/test_config_model_api.py` (owner client). Seed a snapshot (encrypt with `crypto.encrypt_bytes(gzip.compress(xml.encode()))`) containing a secret, then:
- `GET .../config/model` returns the tree; assert the secret string does NOT appear anywhere in the JSON and the password node has `sensitive: true, value: null`.
- `GET .../config/capabilities` returns interfaces/sections/version; mock the device probe (override `get_prober`-style is not applicable — instead inject via the connector being unreachable → resilient empirical-only, OR mock the OpnsenseClient). Simplest: test the capabilities endpoint with a device whose probe fails (connector error) and assert it still returns empirical data (interfaces/sections) with empty `available_capabilities` (resilience).
- 404 when the device has no snapshot.
- 401 without session; 403 no-membership.

Create `tests/test_config_model_rls_api.py` (real `opngms_app`): two tenants with snapshots; tenant A's `GET .../config/model` works for A's device and another tenant's device id returns 404 (RLS hides it). Mirror `test_config_rls_api.py`.

**Probe in tests:** the capabilities endpoint builds an `OpnsenseClient` and calls `get_plugin_info`. In tests, the device is unreachable → `get_plugin_info` raises `OpnsenseError`; the endpoint must catch it and degrade to empirical-only (empty plugins). Assert that path. (No real HTTP in unit tests.)

- [ ] **Step 4: Implement the endpoints + register**

In `app/api/config.py`, add imports and endpoints. Reuse `_xml(snapshot)`.
```python
from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto  # already imported
from app.services.capability import build_inventory
from app.services.config_model import build_tree
from app.schemas.config import CapabilityInventory
```
```python
@router.get("/devices/{device_id}/config/model", response_model=dict)
async def config_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    snap = await ConfigSnapshotRepository(session, tenant_id).latest(device_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="No config snapshot for device")
    return build_tree(_xml(snap))


@router.get("/devices/{device_id}/config/capabilities", response_model=CapabilityInventory)
async def config_capabilities(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> CapabilityInventory:
    repo = ConfigSnapshotRepository(session, tenant_id)
    snap = await repo.latest(device_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="No config snapshot for device")
    # Live probe; degrade gracefully to empirical-only on any connector error.
    plugin_info: dict = {"plugins": []}
    device = await session.get(Device, device_id)  # import Device
    if device is not None:
        try:
            client = OpnsenseClient(
                device.base_url,
                crypto.decrypt(device.api_key_enc),
                crypto.decrypt(device.api_secret_enc),
                verify_tls=device.verify_tls,
            )
            plugin_info = await client.get_plugin_info()
        except OpnsenseError:
            plugin_info = {"plugins": []}
    inv = build_inventory(_xml(snap), snap.opnsense_version, plugin_info)
    return CapabilityInventory(**inv)
```
Add `from app.models.device import Device` to the imports. The router is already registered in `main.py` (4A).

- [ ] **Step 5: Run + alembic check**

Run: `... pytest tests/test_config_model_api.py tests/test_config_model_rls_api.py -v` → PASS. Whole suite green. `alembic check` clean (4B adds no migration).

- [ ] **Step 6: Commit**
```bash
git add app/repositories/config_snapshot.py app/schemas/config.py app/api/config.py \
        tests/test_config_model_api.py tests/test_config_model_rls_api.py
git commit -m "feat(backend): config model + capabilities API (latest snapshot, secret-safe, RLS)"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Record the 4B debt**

Append to this plan:
```markdown
## Technical debt (4B)

- **Plugin/version endpoint TO VERIFY**: `core/firmware/info` + payload shape unconfirmed; confirm
  against a real device. Probe failure already degrades to empirical-only.
- **Sensitive denylist completeness** (security-critical): tag-substring denylist may miss an oddly
  named secret field → leak. Maintain conservatively (over-redact); consider a value-shape heuristic
  (long base64 / hash-looking) as a secondary guard.
- **Attribute values not redacted**: redaction targets leaf element text; a secret carried in an XML
  attribute would not be caught. Rare in OPNsense config; revisit if needed.
- **Minor parse duplication**: `config_model`/`capability` each parse with defusedxml + strip
  `<revision>` (small overlap with `config_diff`). Extract a shared `parse_config` helper if it grows.
- **No field-level schema**: capability is plugin/section level; the per-field editable schema (for 4D
  forms) is deferred and best sourced from the device.
- **Model recomputed per request**: on-demand parse of the latest snapshot each call (acceptable —
  config is small); cache if it ever matters.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase4-milestone4B-config-model.md
git commit -m "docs: technical debt milestone 4B"
```

---

## Definition of "Done" (4B)
- `GET /config/model` returns a schema-agnostic navigable tree of the device's latest config, with sensitive values **redacted** (flagged, never emitted), order-preserving, defusedxml-safe.
- `GET /config/capabilities` returns interfaces, configured sections, OPNsense version, and available plugins/modules (empirical + live probe), resilient to probe failure.
- Both tenant-scoped + RLS-isolated (proven by a real-`opngms_app` test); no secret value in any response.
- Suite green + `alembic check` clean.

---

## Technical debt (4B) — consolidated from reviews

- **Plugin/version endpoint TO VERIFY**: `core/firmware/info` + payload shape unconfirmed; confirm
  against a real device. Probe failure already degrades to empirical-only.
- **Sensitive denylist completeness** (security-critical): the tag-substring denylist covers
  `privkey`/`hash`/`seed`/... (a real private-key leak was closed in review; the over-broad `crypt` was
  dropped because it matched `encryption`). An oddly named secret field could still slip through.
  Maintain conservatively (over-redact); consider a value-shape heuristic (long base64 / hash-looking)
  as a secondary guard.
- *(Resolved in review)* Sensitive **subtrees** and **attribute values** are now redacted: once a tag
  is sensitive the whole subtree is flagged + its values nulled, and attribute values are redacted when
  the attribute key is sensitive or under a redacted subtree.
- **Non-dict package element** (review Task 2): `get_plugin_info` assumes each package is a dict; a
  malformed/hostile device payload (list of strings) would raise an unwrapped `AttributeError`. The
  `/config/capabilities` endpoint catches `OpnsenseError`/`InvalidToken` but not that. Add an
  `isinstance(p, dict)` guard in the connector (cheap) or broaden the endpoint's except.
- **Minor parse duplication**: `config_model`/`capability` each parse with defusedxml + strip
  `<revision>` (overlap with `config_diff`). Extract a shared `parse_config` helper if it grows.
- **No field-level schema**: capability is plugin/section level; the per-field editable schema (for 4D
  forms) is deferred and best sourced from the device.
- **Model recomputed per request**: on-demand parse of the latest snapshot each call (acceptable —
  config is small); cache if it ever matters.

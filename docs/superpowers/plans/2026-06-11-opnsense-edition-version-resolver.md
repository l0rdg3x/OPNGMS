# OPNsense (edition, version) Capability Resolver — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the OPNsense connector resolve every capability's endpoint+parser through a declarative `(edition, version)` matrix, detecting and persisting each device's edition/version/series.

**Architecture:** A declarative matrix (`profiles.py`) maps capability → ordered version/edition-ranged `EndpointSpec`s; a `resolver.py` picks the spec for a device's `(edition, version)`; `client.py` becomes resolver-driven (existing pure parsers in `parsers.py` unchanged). Detection reads `core/firmware/status`; edition/version/series persist on the device.

**Tech Stack:** Python 3.14, SQLAlchemy/Alembic, pytest + respx. The version format is OPNsense `YY.M.point[_hotfix]`.

**Spec:** `docs/superpowers/specs/2026-06-11-opnsense-edition-version-resolver-design.md`
**Branch:** `feat/opnsense-version-resolver` (created; spec committed there).

**Run tests:** `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest <files> -q`. Pure/respx tests need no DB. DB-touching tests (migration, monitoring, poller) prefix: `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`. Files & comments in English; commit messages end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer.

---

## File Structure

- **Create:** `backend/app/connectors/opnsense/profiles.py` (Request/EndpointSpec/ProfileRule + CAPABILITIES), `backend/app/connectors/opnsense/resolver.py` (CapabilityResolver), `backend/app/connectors/opnsense/identity.py` (DeviceIdentity + parse_identity), `backend/migrations/versions/0016_device_edition.py`, tests `test_opnsense_version.py`, `test_resolver.py`, `test_profiles.py`, `test_identity.py`, fixture `backend/tests/fixtures/opnsense/firmware_status_business.json`.
- **Modify:** `parsers.py` (+`parse_version`/`series_of`), `client.py` (resolver-driven), `models/device.py` (+`edition`/`firmware_series`), `services/onboarding.py` + `api/devices.py` (probe→persist identity), `services/monitoring.py` (detect+persist+profile), `services/capability.py` (edition in inventory), `scripts/verify_opnsense_live.py` (print identity), test stubs in `test_monitoring.py`/`test_poller_e2e.py`.

---

## Task 1: Hotfix-aware version helpers

**Files:** Modify `backend/app/connectors/opnsense/parsers.py`; Create `backend/tests/test_opnsense_version.py`.

- [ ] **Step 1: Write failing tests** — create `backend/tests/test_opnsense_version.py`:

```python
from app.connectors.opnsense import parsers


def test_parse_version_basic_and_hotfix():
    assert parsers.parse_version("26.1.9") == (26, 1, 9, 0)
    assert parsers.parse_version("26.1.9_1") == (26, 1, 9, 1)
    assert parsers.parse_version("24.7.1_2") == (24, 7, 1, 2)
    assert parsers.parse_version("26.1") == (26, 1, 0, 0)


def test_parse_version_ordering():
    assert parsers.parse_version("26.1.9_1") > parsers.parse_version("26.1.9")
    assert parsers.parse_version("26.1.9") > parsers.parse_version("26.1.8")
    assert parsers.parse_version("26.1.0") > parsers.parse_version("25.7.5_9")


def test_parse_version_defensive():
    assert parsers.parse_version("") == (0, 0, 0, 0)
    assert parsers.parse_version(None) == (0, 0, 0, 0)
    assert parsers.parse_version("garbage") == (0, 0, 0, 0)


def test_series_of():
    assert parsers.series_of("26.1.9_1") == "26.1"
    assert parsers.series_of("24.7") == "24.7"
    assert parsers.series_of("") == "0.0"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_opnsense_version.py -q`
Expected: FAIL (AttributeError: module has no attribute 'parse_version').

- [ ] **Step 3: Append to `backend/app/connectors/opnsense/parsers.py`:**

```python
def parse_version(s) -> tuple[int, int, int, int]:
    """OPNsense 'YY.M.point[_hotfix]' -> (year, month, point, hotfix); missing parts -> 0.

    The '_hotfix' suffix (e.g. 24.7.1_2) is not PEP 440, so this is parsed by hand. Defensive:
    non-numeric / unexpected input never raises (best-effort, 0-filled)."""
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
    """'26.1.9_1' -> '26.1' (the YY.M series; point/hotfix ignored)."""
    y, m, _, _ = parse_version(s)
    return f"{y}.{m}"
```

(`re` is already imported at the top of `parsers.py`.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_opnsense_version.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/parsers.py backend/tests/test_opnsense_version.py
git commit -m "feat(opnsense): hotfix-aware version parsing (YY.M.point[_hotfix])

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Resolver framework (dataclasses + CapabilityResolver)

**Files:** Create `backend/app/connectors/opnsense/profiles.py` (dataclasses + an empty/test matrix is NOT needed yet — define the types only), `backend/app/connectors/opnsense/resolver.py`; Create `backend/tests/test_resolver.py`.

**Context:** This task builds and unit-tests the resolution MECHANISM with controlled synthetic rules — it does not depend on the real CAPABILITIES table (added in Task 3). To test the resolver in isolation, the test injects its own rules dict.

- [ ] **Step 1: Define the dataclasses** — create `backend/app/connectors/opnsense/profiles.py`:

```python
"""Declarative (edition, version) capability matrix for the OPNsense connector.

Each capability maps to an ordered list of ProfileRule; the resolver returns the EndpointSpec
of the first rule whose (edition, version-range) matches a device. The LAST rule of every
capability MUST be the unconstrained default (edition="any", no bounds).
"""
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Request:
    method: str             # "GET" | "POST"
    path: str               # e.g. "diagnostics/traffic/interface" (may include a ?query)
    body: dict | None = None
    kind: str = "json"      # "json" | "text"


@dataclass(frozen=True)
class EndpointSpec:
    requests: tuple          # tuple[Request, ...] — 1 for most capabilities; 4 for system_info
    combine: Callable        # combine(list_of_decoded_responses) -> normalized result


@dataclass(frozen=True)
class ProfileRule:
    edition: str             # "community" | "business" | "devel" | "any"
    min_version: tuple | None  # inclusive lower bound (parse_version tuple) or None
    max_version: tuple | None  # EXCLUSIVE upper bound or None
    spec: EndpointSpec


# The real CAPABILITIES matrix is added in Task 3.
CAPABILITIES: dict[str, list[ProfileRule]] = {}
```

- [ ] **Step 2: Write failing resolver tests** — create `backend/tests/test_resolver.py`:

```python
from app.connectors.opnsense.profiles import EndpointSpec, ProfileRule, Request
from app.connectors.opnsense.resolver import CapabilityResolver


def _spec(tag):
    # a marker spec we can identify by its single request path
    return EndpointSpec(requests=(Request("GET", tag),), combine=lambda r: r)


def _rules():
    return {
        "cap": [
            ProfileRule("business", None, None, _spec("biz")),
            ProfileRule("any", None, (20, 1, 0, 0), _spec("legacy")),
            ProfileRule("any", (24, 7, 0, 0), None, _spec("modern")),
            ProfileRule("any", None, None, _spec("default")),
        ],
    }


def _path(resolver, cap):
    return resolver.resolve(cap).requests[0].path


def test_edition_takes_priority():
    r = CapabilityResolver("business", "26.1.9", rules=_rules())
    assert _path(r, "cap") == "biz"


def test_legacy_below_max():
    r = CapabilityResolver("community", "18.7.1", rules=_rules())
    assert _path(r, "cap") == "legacy"


def test_modern_at_or_above_min():
    r = CapabilityResolver("community", "24.7.0", rules=_rules())   # inclusive min
    assert _path(r, "cap") == "modern"


def test_hotfix_boundary():
    # max is exclusive (20,1,0,0); 20.1.0 is NOT legacy, falls through to default
    r = CapabilityResolver("community", "20.1.0", rules=_rules())
    assert _path(r, "cap") == "default"
    # but 20.0.9_9 is still below the bound -> legacy
    r2 = CapabilityResolver("community", "20.0.9_9", rules=_rules())
    assert _path(r2, "cap") == "legacy"


def test_unknown_version_uses_newest():
    # empty/garbage version -> NEWEST sentinel -> never matches a bounded-max rule
    r = CapabilityResolver("community", "", rules=_rules())
    assert _path(r, "cap") == "modern"   # min (24,7) satisfied by NEWEST, before default


def test_resolve_never_returns_none():
    r = CapabilityResolver("community", "1.0", rules={"cap": [
        ProfileRule("any", None, None, _spec("only"))]})
    assert _path(r, "cap") == "only"
```

- [ ] **Step 3: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_resolver.py -q`
Expected: FAIL (ModuleNotFoundError: resolver).

- [ ] **Step 4: Implement** — create `backend/app/connectors/opnsense/resolver.py`:

```python
"""Resolve a capability to its concrete EndpointSpec for a given device (edition, version)."""
from app.connectors.opnsense import parsers
from app.connectors.opnsense.profiles import CAPABILITIES, EndpointSpec

# Unknown/unparseable version -> assume the newest profile (most likely correct for a 2026+
# fleet; never selects a legacy rule with a bounded max_version).
_NEWEST = (9999, 99, 99, 99)


class CapabilityResolver:
    def __init__(self, edition: str, version: str, rules: dict | None = None) -> None:
        self.edition = (edition or "community").strip().lower()
        v = parsers.parse_version(version)
        self.vtuple = _NEWEST if v == (0, 0, 0, 0) else v
        self._rules = rules if rules is not None else CAPABILITIES

    def resolve(self, capability: str) -> EndpointSpec:
        rules = self._rules[capability]
        for rule in rules:
            if rule.edition not in ("any", self.edition):
                continue
            if rule.min_version is not None and self.vtuple < rule.min_version:
                continue
            if rule.max_version is not None and self.vtuple >= rule.max_version:
                continue
            return rule.spec
        return rules[-1].spec   # guaranteed: last rule is the unconstrained default
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_resolver.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/profiles.py backend/app/connectors/opnsense/resolver.py backend/tests/test_resolver.py
git commit -m "feat(opnsense): capability resolver mechanism + profile dataclasses

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: The CAPABILITIES matrix (verified 26.1.x + DNS legacy)

**Files:** Modify `backend/app/connectors/opnsense/profiles.py`; Create `backend/tests/test_profiles.py`.

- [ ] **Step 1: Write failing tests** — create `backend/tests/test_profiles.py`:

```python
from app.connectors.opnsense.profiles import CAPABILITIES
from app.connectors.opnsense.resolver import CapabilityResolver

CAPS = ["system_info", "interfaces", "gateways", "vpn_status", "ids_alerts",
        "dns_events", "firmware_status", "plugin_info", "config_backup"]


def test_every_capability_has_an_unconstrained_default_last():
    for name in CAPS:
        last = CAPABILITIES[name][-1]
        assert last.edition == "any" and last.min_version is None and last.max_version is None


def test_current_device_resolves_to_verified_261_endpoints():
    r = CapabilityResolver("community", "26.1.9")
    paths = {name: [req.path for req in r.resolve(name).requests] for name in CAPS}
    assert paths["interfaces"] == ["diagnostics/traffic/interface"]
    assert paths["gateways"] == ["routes/gateway/status"]
    assert paths["vpn_status"] == ["wireguard/service/show"]
    assert paths["ids_alerts"] == ["ids/service/queryAlerts"]
    assert paths["dns_events"][0].startswith("unbound/overview/searchQueries")
    assert paths["plugin_info"] == ["core/firmware/info"]
    assert paths["firmware_status"] == ["core/firmware/status"]
    assert paths["config_backup"] == ["core/backup/download/this"]
    assert paths["system_info"] == [
        "diagnostics/system/systemResources", "diagnostics/system/systemDisk",
        "diagnostics/system/systemTime", "diagnostics/cpu_usage/getCPUType"]


def test_old_series_resolves_dns_to_legacy_endpoint():
    r = CapabilityResolver("community", "18.7.1")
    assert r.resolve("dns_events").requests[0].path == "unbound/diagnostics/queries"


def test_ids_request_is_post_with_body():
    req = CapabilityResolver("community", "26.1.9").resolve("ids_alerts").requests[0]
    assert req.method == "POST" and req.body["searchPhrase"] == ""


def test_config_backup_is_text():
    req = CapabilityResolver("community", "26.1.9").resolve("config_backup").requests[0]
    assert req.kind == "text"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_profiles.py -q`
Expected: FAIL (KeyError on CAPABILITIES["system_info"], since the matrix is empty).

- [ ] **Step 3: Populate the matrix** — in `backend/app/connectors/opnsense/profiles.py`, add imports at the top (after the existing imports) and replace the empty `CAPABILITIES = {}` with the full table:

```python
from app.connectors.opnsense import parsers

# Rows requested from the paged IDS/DNS query endpoints (dedup happens downstream).
MAX_QUERY_ROWS = 500


def _GET(path: str, kind: str = "json") -> Request:
    return Request("GET", path, None, kind)


def _POST(path: str, body: dict) -> Request:
    return Request("POST", path, body, "json")


def _spec(*requests: Request, combine: Callable) -> EndpointSpec:
    return EndpointSpec(requests=tuple(requests), combine=combine)


def _default(spec: EndpointSpec) -> ProfileRule:
    return ProfileRule("any", None, None, spec)


CAPABILITIES: dict[str, list[ProfileRule]] = {
    "system_info": [_default(_spec(
        _GET("diagnostics/system/systemResources"),
        _GET("diagnostics/system/systemDisk"),
        _GET("diagnostics/system/systemTime"),
        _GET("diagnostics/cpu_usage/getCPUType"),
        combine=lambda r: parsers.parse_system_info(r[0], r[1], r[2], r[3])))],
    "interfaces": [_default(_spec(
        _GET("diagnostics/traffic/interface"),
        combine=lambda r: parsers.parse_interfaces(r[0])))],
    "gateways": [_default(_spec(
        _GET("routes/gateway/status"),
        combine=lambda r: parsers.parse_gateways(r[0])))],
    "vpn_status": [_default(_spec(
        _GET("wireguard/service/show"),
        combine=lambda r: parsers.parse_vpn(r[0])))],
    "ids_alerts": [_default(_spec(
        _POST("ids/service/queryAlerts",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_ids_rows(r[0])))],
    "dns_events": [
        # Legacy pre-rename endpoint for old series. The (20,1) boundary is best-effort
        # (no pre-rename hardware available); it documents and exercises the matrix.
        ProfileRule("any", None, (20, 1, 0, 0), _spec(
            _GET("unbound/diagnostics/queries"),
            combine=lambda r: parsers.parse_dns_rows(r[0]))),
        _default(_spec(
            _GET(f"unbound/overview/searchQueries?current=1&rowCount={MAX_QUERY_ROWS}"),
            combine=lambda r: parsers.parse_dns_rows(r[0]))),
    ],
    "firmware_status": [_default(_spec(
        _GET("core/firmware/status"),
        combine=lambda r: parsers.parse_firmware_version(r[0])))],
    "plugin_info": [_default(_spec(
        _GET("core/firmware/info"),
        combine=lambda r: parsers.parse_plugins(r[0])))],
    "config_backup": [_default(_spec(
        _GET("core/backup/download/this", kind="text"),
        combine=lambda r: r[0]))],
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_profiles.py tests/test_resolver.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/profiles.py backend/tests/test_profiles.py
git commit -m "feat(opnsense): capability matrix (verified 26.1.x specs + DNS legacy row)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Edition/version detection (identity)

**Files:** Create `backend/app/connectors/opnsense/identity.py`, `backend/tests/fixtures/opnsense/firmware_status_business.json`, `backend/tests/test_identity.py`.

- [ ] **Step 1: Create the synthetic Business fixture** `backend/tests/fixtures/opnsense/firmware_status_business.json` (inferred; values confirmed when a real Business box is available):

```json
{"product":{"product_id":"opnsense-business","product_name":"OPNsense Business","product_version":"24.10.2","product_series":"24.10","product_tier":"2","product_repos":"OPNsense Business (Priority: 11)"},"status_msg":"...","status":"none"}
```

- [ ] **Step 2: Write failing tests** — create `backend/tests/test_identity.py`:

```python
from app.connectors.opnsense.identity import DeviceIdentity, parse_identity
from tests.opn_fixtures import load


def test_parse_identity_community():
    ident = parse_identity(load("firmware_status.json"))
    assert ident == DeviceIdentity(edition="community", version="26.1.9", series="26.1")


def test_parse_identity_business():
    ident = parse_identity(load("firmware_status_business.json"))
    assert ident.edition == "business"
    assert ident.version == "24.10.2" and ident.series == "24.10"


def test_parse_identity_series_fallback_from_version():
    ident = parse_identity({"product": {"product_id": "opnsense", "product_version": "25.7.3_1"}})
    assert ident.series == "25.7" and ident.version == "25.7.3_1"


def test_parse_identity_defensive():
    assert parse_identity({}).edition == "community"
    assert parse_identity(None).version == ""
```

- [ ] **Step 3: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_identity.py -q`
Expected: FAIL (ModuleNotFoundError: identity).

- [ ] **Step 4: Implement** — create `backend/app/connectors/opnsense/identity.py`:

```python
"""Detect an OPNsense device's edition + version from core/firmware/status."""
from dataclasses import dataclass

from app.connectors.opnsense import parsers


@dataclass(frozen=True)
class DeviceIdentity:
    edition: str   # "community" | "business" | "devel"
    version: str   # e.g. "26.1.9" or "26.1.9_1"
    series: str    # e.g. "26.1"


def parse_identity(firmware_status: dict) -> DeviceIdentity:
    """Map a core/firmware/status payload to a DeviceIdentity. Never raises.

    Edition signal: product_id ("opnsense" vs "opnsense-business" vs "opnsense-devel"), with a
    defensive fallback to product_repos/product_name containing "business". Business values are
    inferred pending a real Business box."""
    product = (firmware_status or {}).get("product", {}) or {}
    pid = str(product.get("product_id", "")).lower()
    blob = f"{pid} {str(product.get('product_repos', '')).lower()} {str(product.get('product_name', '')).lower()}"
    if "business" in blob:
        edition = "business"
    elif "devel" in pid:
        edition = "devel"
    else:
        edition = "community"
    version = product.get("product_version") or ""
    series = product.get("product_series") or parsers.series_of(version)
    return DeviceIdentity(edition=edition, version=version, series=series)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_identity.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/identity.py backend/tests/test_identity.py backend/tests/fixtures/opnsense/firmware_status_business.json
git commit -m "feat(opnsense): edition/version detection from firmware/status

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Device model columns + migration 0016

**Files:** Modify `backend/app/models/device.py`; Create `backend/migrations/versions/0016_device_edition.py`; Create `backend/tests/test_migration_0016.py`.

- [ ] **Step 1: Add the model columns** — in `backend/app/models/device.py`, after the `firmware_version` line (line 29), add:

```python
    edition: Mapped[str] = mapped_column(default="", server_default="")
    firmware_series: Mapped[str] = mapped_column(default="", server_default="")
```

- [ ] **Step 2: Create the migration** `backend/migrations/versions/0016_device_edition.py`:

```python
"""device edition + firmware_series

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("edition", sa.String(), nullable=False, server_default=""))
    op.add_column("devices", sa.Column("firmware_series", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("devices", "firmware_series")
    op.drop_column("devices", "edition")
```

(Confirm `0015` is the current head: `cd backend && .venv/bin/python -m alembic heads`. If the head differs, set `down_revision` to the actual head and `revision` to the next number.)

- [ ] **Step 3: Write the migration test** — create `backend/tests/test_migration_0016.py`:

```python
from sqlalchemy import text


async def test_device_edition_columns_exist(db_engine):
    async with db_engine.connect() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='devices'"
        ))).scalars().all()
    assert "edition" in cols
    assert "firmware_series" in cols
```

- [ ] **Step 4: Run the migration test**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_migration_0016.py -q`
Expected: PASS (the `db_engine` fixture builds the schema from the models via create_all, so the new columns exist).

- [ ] **Step 5: Verify the Alembic migration applies cleanly on a fresh DB**

Run:
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
docker compose exec -T db dropdb -U opngms --if-exists opngms_migcheck
docker compose exec -T db createdb -U opngms opngms_migcheck
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_migcheck .venv/bin/python -m alembic upgrade head
```
Expected: ends at revision 0016 with no error.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/models/device.py backend/migrations/versions/0016_device_edition.py backend/tests/test_migration_0016.py
git commit -m "feat(device): edition + firmware_series columns (migration 0016)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Make the client resolver-driven

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Create `backend/tests/test_connector_resolver.py`.

**Context:** Convert the 9 capability methods to delegate to the resolver. A client with no identity defaults to the newest profile (so all existing respx tests — which mock the current endpoints — keep passing with no change). Identity is set explicitly by callers (Task 7).

- [ ] **Step 1: Write the failing parametrized test** — create `backend/tests/test_connector_resolver.py`:

```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _client(**kw):
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False, **kw)


@respx.mock
async def test_default_client_uses_current_endpoints():
    respx.get(url__regex=r".*/api/unbound/overview/searchQueries.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    assert await _client().get_dns_events() == []   # no identity -> newest -> current endpoint


@respx.mock
async def test_old_series_client_uses_legacy_dns_endpoint():
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"client": "10.0.0.7", "domain": "x.com", "action": "allowed"}]}))
    out = await _client(version="18.7.1").get_dns_events()
    assert out[0]["name"] == "x.com"


@respx.mock
async def test_set_identity_switches_profile():
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    c = _client()
    c.set_identity("community", "19.1.0")
    assert await c.get_dns_events() == []   # now resolves to the legacy endpoint (mocked)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_resolver.py -q`
Expected: FAIL (TypeError: unexpected keyword 'version' / no set_identity).

- [ ] **Step 3: Refactor the client** — in `backend/app/connectors/opnsense/client.py`:

(a) Add imports near the top (after the existing `from app.connectors.opnsense import parsers`):

```python
from app.connectors.opnsense.identity import DeviceIdentity, parse_identity
from app.connectors.opnsense.resolver import CapabilityResolver
```

(b) In `__init__`, add two keyword params and build the resolver. Change the signature to include `edition: str = "", version: str = ""` (place them after `timeout`):

```python
        edition: str = "",
        version: str = "",
```

and at the end of `__init__` body add:

```python
        self._resolver = CapabilityResolver(edition, version)
```

(c) Add these methods (anywhere in the class, e.g. after `_post`):

```python
    def set_identity(self, edition: str, version: str) -> None:
        """Switch the resolver to a device's detected (edition, version)."""
        self._resolver = CapabilityResolver(edition, version)

    async def get_device_identity(self) -> DeviceIdentity:
        """Detect edition/version/series from core/firmware/status."""
        return parse_identity(await self._get("core/firmware/status"))

    def _decode(self, resp):
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("response not interpretable") from exc

    async def _capability(self, name: str):
        """Resolve a capability to its EndpointSpec, issue its request(s), and combine."""
        spec = self._resolver.resolve(name)
        responses = []
        for req in spec.requests:
            resp = await self._request(req.path, req.method, req.body)
            responses.append(resp.text if req.kind == "text" else self._decode(resp))
        return spec.combine(responses)
```

(d) Replace the bodies of the nine capability methods with thin delegations (keep their signatures, incl. the unused `since`):

```python
    async def get_system_info(self) -> dict:
        return await self._capability("system_info")

    async def get_interfaces(self) -> list[dict]:
        return await self._capability("interfaces")

    async def get_gateways(self) -> list[dict]:
        return await self._capability("gateways")

    async def get_vpn_status(self) -> list[dict]:
        return await self._capability("vpn_status")

    async def get_ids_alerts(self, since: datetime | None = None) -> list[dict]:
        return await self._capability("ids_alerts")

    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        return await self._capability("dns_events")

    async def get_plugin_info(self) -> dict:
        return await self._capability("plugin_info")

    async def get_config_backup(self) -> str:
        return await self._capability("config_backup")

    async def get_firmware_status(self) -> dict:
        return {"product_version": await self._capability("firmware_status")}

    async def test_connection(self) -> str | None:
        return (await self._capability("firmware_status")) or None
```

(e) Remove the now-unused `_post` method (the `_capability` path uses `_request` directly; `_get` stays — it is used by `get_device_identity`). Confirm `_post` has no remaining callers: `cd /home/l0rdg3x/coding/OPNGMS/backend && rg -n "self\._post|\._post\(" app tests` → no matches before removing.

- [ ] **Step 4: Run the new test + the full existing connector suite (must stay green)**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_resolver.py tests/test_connector_system_info.py tests/test_connector_network.py tests/test_connector_ids.py tests/test_connector_dns.py tests/test_connector_plugin_info.py tests/test_connector_config.py tests/test_connector_tls_pinning.py tests/test_opnsense_client.py tests/test_connector_apply_alias.py -q`
Expected: ALL PASS. (Existing tests mock the current endpoints; the default-newest resolver routes to them unchanged. `apply_alias` is untouched by this refactor.)

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_resolver.py
git commit -m "refactor(opnsense): resolver-driven client (endpoints from the version matrix)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Persist identity (onboarding + monitoring) + edition-aware inventory + verify script

**Files:** Modify `backend/app/services/onboarding.py`, `backend/app/api/devices.py`, `backend/app/services/monitoring.py`, `backend/app/services/capability.py`, `scripts/verify_opnsense_live.py`, and the fake-client stubs in `backend/tests/test_monitoring.py` / `backend/tests/test_poller_e2e.py`.

- [ ] **Step 1: Extend the probe to return identity** — replace the body of `backend/app/services/onboarding.py` with:

```python
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError


@dataclass
class ProbeResult:
    reachable: bool
    firmware_version: str | None
    error: str | None
    edition: str = ""
    series: str = ""


async def probe_device(
    base_url: str,
    api_key: str,
    api_secret: str,
    *,
    verify_tls: bool = True,
    tls_fingerprint: str | None = None,
) -> ProbeResult:
    client = OpnsenseClient(base_url, api_key, api_secret, verify_tls=verify_tls, tls_fingerprint=tls_fingerprint)
    try:
        ident = await client.get_device_identity()
        return ProbeResult(reachable=True, firmware_version=ident.version or None,
                           error=None, edition=ident.edition, series=ident.series)
    except OpnsenseError as exc:
        return ProbeResult(reachable=False, firmware_version=None, error=type(exc).__name__)


Prober = Callable[..., Coroutine[Any, Any, ProbeResult]]


def get_prober() -> Prober:
    return probe_device
```

- [ ] **Step 2: Persist edition/series on device create** — in `backend/app/api/devices.py`, where a freshly-created device's probe result is applied (search for `result.firmware_version` / `result.reachable`), set the new fields. After the existing status/firmware assignment add:

```python
    device.edition = result.edition
    device.firmware_series = result.series
```

(Find the exact spot: `cd backend && rg -n "result.firmware_version|result.reachable|status=.*reachable" app/api/devices.py`. Apply right next to where `firmware_version`/`status` are set, for both the create and the re-test paths if both exist.)

- [ ] **Step 3: Detect + persist + apply profile in monitoring** — in `backend/app/services/monitoring.py`, change `collect_and_store` so it detects identity first, applies it to the client, and persists it. Replace the `try:` block that fetches info/fw/... so it begins with identity detection:

```python
    try:
        ident = await client.get_device_identity()
        client.set_identity(ident.edition, ident.version)
        info = await client.get_system_info()
        fw = await client.get_firmware_status()
        interfaces = await client.get_interfaces()
        gateways = await client.get_gateways()
        vpn = await client.get_vpn_status()
    except OpnsenseError:
        device.status = "unverified"
        return PollState(reachable=False)
```

and replace the firmware-version persistence near the end:

```python
    device.status = "reachable"
    device.last_seen = now
    device.edition = ident.edition
    device.firmware_series = ident.series
    version = ident.version or fw.get("product_version")
    if version:
        device.firmware_version = version
    await session.flush()
    return PollState(reachable=True, gateways=gateways)
```

- [ ] **Step 4: Update the fake-client stubs** — the monitoring/poller tests inject stub clients. Add `get_device_identity` and `set_identity` to those stubs so the new flow works. In `backend/tests/test_monitoring.py` and `backend/tests/test_poller_e2e.py`, find each stub/fake client class (search `get_system_info`) and add:

```python
    async def get_device_identity(self):
        from app.connectors.opnsense.identity import DeviceIdentity
        return DeviceIdentity(edition="community", version="26.1.9", series="26.1")

    def set_identity(self, edition, version):
        pass
```

(If a stub is a `unittest.mock`/`SimpleNamespace` rather than a class, add an async `get_device_identity` attribute returning the `DeviceIdentity` and a no-op `set_identity`. Match the existing stub style in each file.)

- [ ] **Step 5: Edition in the capability inventory** — in `backend/app/services/capability.py`, extend `build_inventory` to accept and surface `edition`. Change its signature and returned dict:

```python
def build_inventory(xml: str, opnsense_version: str, plugin_info: dict, edition: str = "") -> dict:
    root = _parse_xml(xml)
    configured_sections = [el.tag for el in list(root) if el.tag != "revision"]
    available = [describe(pid) for pid in plugin_info.get("plugins", [])]
    return {
        "opnsense_version": opnsense_version,
        "edition": edition,
        "interfaces": _interfaces(root),
        "configured_sections": configured_sections,
        "available_capabilities": available,
    }
```

Then update the single caller in `backend/app/api/config.py` (around line 145) to pass the device edition:

```python
    inv = build_inventory(_xml(snap), snap.opnsense_version, plugin_info, edition=device.edition)
```

(Confirm `device` is in scope there: `cd backend && rg -n "build_inventory|device\b" app/api/config.py | head`. If the device object is not in scope at that line, pass `edition=""` — the inventory still gains the key — and note it; do not invent a lookup.)

- [ ] **Step 6: Print identity in the live verify script** — in `scripts/verify_opnsense_live.py`, add an identity line near the start of `main()` (after the client is built, before the `checks` dict):

```python
    ident = await client.get_device_identity()
    print(f"IDENTITY  edition={ident.edition} version={ident.version} series={ident.series}\n")
    client.set_identity(ident.edition, ident.version)
```

- [ ] **Step 7: Run the affected suites**

Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_onboarding.py tests/test_devices_api.py tests/test_monitoring.py tests/test_poller_e2e.py tests/test_capability.py tests/test_config_api.py -q`
Expected: ALL PASS. If a device-API test asserts an exact response body, add `edition`/`firmware_series` to its expectation.

- [ ] **Step 8: Live end-to-end against the real box** (read-only)

Run:
```bash
cd /home/l0rdg3x/coding/OPNGMS
OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=/home/l0rdg3x/Scaricati/OPNsense.internal_root_apikey.txt backend/.venv/bin/python scripts/verify_opnsense_live.py
```
Expected: `IDENTITY edition=community version=26.1.9 series=26.1`, then `ALL PASS`.

- [ ] **Step 9: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/onboarding.py backend/app/api/devices.py backend/app/services/monitoring.py backend/app/services/capability.py backend/app/api/config.py scripts/verify_opnsense_live.py backend/tests/test_monitoring.py backend/tests/test_poller_e2e.py
git commit -m "feat(opnsense): detect+persist device edition/version, edition-aware inventory

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full backend suite green: `cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`
- [ ] No unused `_post` left: `rg -n "_post" backend/app/connectors/opnsense/client.py` → no matches
- [ ] Live verify prints the right identity and `ALL PASS`
- [ ] Dispatch a final holistic review, then superpowers:finishing-a-development-branch.

---

## Self-Review (author)

**Spec coverage:** version parsing incl. hotfix (Task 1); resolver mechanism (Task 2); the matrix with verified 26.1.x + DNS legacy (Task 3); edition/version detection (Task 4); device columns + migration (Task 5); resolver-driven client (Task 6); persistence + edition-aware inventory + verify script (Task 7). Business-inference, the approximate DNS boundary, and the resolver-never-None invariant are all realized and tested.

**Placeholder scan:** every code step is complete; commands have expected output; the two "find the exact spot" notes (devices.py persistence, config.py device scope) give a concrete grep + a defined fallback, not a vague TODO.

**Type consistency:** `parse_version`/`series_of` (Task 1) used by `resolver`/`identity`/`profiles`; `CapabilityResolver(edition, version, rules=None)`, `EndpointSpec.requests`/`.combine`, `ProfileRule(edition, min_version, max_version, spec)`, `Request(method, path, body, kind)` consistent across Tasks 2/3/6; `DeviceIdentity(edition, version, series)` consistent across Tasks 4/6/7; `client.set_identity`/`get_device_identity`/`_capability` consistent across Tasks 6/7; `build_inventory(..., edition="")` matched at its caller.

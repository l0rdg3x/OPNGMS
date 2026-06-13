# Plugin Coverage — Phase 2 Implementation Plan (per-device install telemetry)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist, per device, the full list of plugins the box reports (installed + available-to-install, each with version + install state), refreshed on every poll, and expose it via the API — so the Plugins UI (Phase 4) can list plugins and badge which are installed.

**Architecture:** OPNGMS already parses `core/firmware/info` into installed plugin *names* (`parse_plugins`), but discards the rest and persists nothing. This phase (a) extends `parse_plugins` to also return the full plugin list with per-plugin install state, (b) adds a JSONB `devices.installed_plugins` column, (c) writes it on every poll (best-effort), and (d) exposes it on a dedicated per-device endpoint.

**Tech Stack:** Python 3.14, SQLAlchemy async + a JSONB column + an Alembic migration, FastAPI, pytest (asyncio auto, schema built via `Base.metadata.create_all`).

**Branch:** `feat/plugin-telemetry` (already created off `main`).

**Spec:** `docs/superpowers/specs/2026-06-13-plugin-catalog-coverage-design.md` (Phase 2).

> **Scope note — Phase 3 already exists.** The design's "Phase 3 — gated install/remove apply kind" is **already implemented** in the codebase: the connector has `OpnsenseClient.plugin_install`/`plugin_remove` (charset-validated, `backend/app/connectors/opnsense/client.py`), the `FirmwareAction` model has `plugin_install`/`plugin_remove` kinds, `backend/app/services/firmware_action.py` runs them (gated on "device must be up to date", serialized per-device via advisory lock, reboot-tolerant), and `POST /api/tenants/{tid}/devices/{did}/firmware/action` exposes it with `Action.CONFIG_PUSH` RBAC + scheduling. **Do NOT build a redundant `plugin_lifecycle` apply kind.** After this Phase 2, the only remaining work is **Phase 4 (UI)**, which wires a Plugins page to *this* telemetry + the *existing* firmware/action endpoint + the plugins catalog from Phase 1.

**Backend test env** (TimescaleDB is up; schema is built from the models via `create_all`):
```
cd /home/l0rdg3x/coding/OPNGMS/backend
. .venv/bin/activate
export ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
```
Lint gate: `ruff check app/`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `backend/app/connectors/opnsense/parsers.py` | `parse_plugins`: NEW `available` list (full install-state), `plugins` (installed names) unchanged | Modify |
| `backend/app/models/device.py` | NEW JSONB `installed_plugins` column | Modify |
| `backend/migrations/versions/0033_device_installed_plugins.py` | Prod migration adding the column | Create |
| `backend/app/services/monitoring.py` | `collect_and_store`: best-effort persist `device.installed_plugins` each poll | Modify |
| `backend/app/schemas/device.py` | NEW `PluginInfoOut` response model | Modify |
| `backend/app/api/devices.py` | NEW `GET /{device_id}/plugins` endpoint | Modify |
| `backend/tests/test_opnsense_parsers.py` | Tests for the `available` list | Modify |
| `backend/tests/test_monitoring.py` | Test telemetry persistence | Modify |
| `backend/tests/test_device_plugins_api.py` | Test the new endpoint | Create |

---

## Task 1: `parse_plugins` returns the full install-state list

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py:156-169`
- Test: `backend/tests/test_opnsense_parsers.py`

Keep `plugins` (installed names — `capability.build_inventory` relies on it) **unchanged**; add `available`: every reported plugin as `{name, installed(bool), version, locked(bool)}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_opnsense_parsers.py`:

```python
def test_parse_plugins_available_lists_all_with_install_state():
    info = {"product_version": "26.1.9", "plugin": [
        {"name": "os-wireguard", "installed": "1", "version": "2.6", "locked": "0"},
        {"name": "os-theme-cicada", "installed": "0", "version": "1.40"},
    ]}
    out = parsers.parse_plugins(info)
    assert out["plugins"] == ["os-wireguard"]                 # unchanged: installed names only
    avail = {p["name"]: p for p in out["available"]}
    assert set(avail) == {"os-wireguard", "os-theme-cicada"}
    assert avail["os-wireguard"] == {
        "name": "os-wireguard", "installed": True, "version": "2.6", "locked": False}
    assert avail["os-theme-cicada"]["installed"] is False
    assert avail["os-theme-cicada"]["version"] == "1.40"


def test_parse_plugins_available_tolerates_malformed():
    assert parsers.parse_plugins({"plugin": None})["available"] == []
    assert parsers.parse_plugins({})["available"] == []
    assert parsers.parse_plugins(None)["available"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_opnsense_parsers.py::test_parse_plugins_available_lists_all_with_install_state -q`
Expected: FAIL — `KeyError: 'available'`.

- [ ] **Step 3: Implement**

Replace `parse_plugins` in `backend/app/connectors/opnsense/parsers.py` with:

```python
def parse_plugins(info: dict) -> dict:
    """firmware/info -> {product_version, plugins, available}.

    `plugins` keeps only INSTALLED plugin names (backward-compatible — the inventory builder relies on
    it). `available` is EVERY plugin the box reports, each as {name, installed(bool), version,
    locked(bool)} — the full install-state list the Plugins UI needs. Reads the `plugin` array
    (OPNsense plugins), NOT the much larger `package` array.
    """
    info = info or {}
    raw = info.get("plugin")
    items = [p for p in (raw if isinstance(raw, list) else []) if isinstance(p, dict) and p.get("name")]
    available = [
        {
            "name": p.get("name", ""),
            "installed": str(p.get("installed", "")) in ("1", "true", "True"),
            "version": p.get("version", ""),
            "locked": _truthy(p.get("locked")),
        }
        for p in items
    ]
    plugins = [a["name"] for a in available if a["installed"]]
    return {"product_version": parse_firmware_version(info), "plugins": plugins, "available": available}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_opnsense_parsers.py -q`
Expected: PASS (the new tests + the existing `test_parse_plugins_*` tests — `plugins` output is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/tests/test_opnsense_parsers.py
git commit -m "feat(connector): parse_plugins also returns the full plugin install-state list"
```

---

## Task 2: `devices.installed_plugins` JSONB column + migration

**Files:**
- Modify: `backend/app/models/device.py`
- Create: `backend/migrations/versions/0033_device_installed_plugins.py`
- Test: `backend/tests/test_monitoring.py` (column default roundtrip)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_monitoring.py`:

```python
async def test_device_installed_plugins_defaults_to_empty_list(db_engine):
    tenant_id, device_id = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, device_id)
        assert device.installed_plugins == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_monitoring.py::test_device_installed_plugins_defaults_to_empty_list -q`
Expected: FAIL — `AttributeError: 'Device' object has no attribute 'installed_plugins'` (or a DB error on the missing column).

- [ ] **Step 3: Add the model column**

In `backend/app/models/device.py`, update the imports:

```python
from sqlalchemy import ARRAY, DateTime, ForeignKey, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
```

and add the column after `firmware_series`:

```python
    firmware_series: Mapped[str] = mapped_column(default="", server_default="")
    # Plugins the box reports (installed AND available-to-install), each {name, installed, version,
    # locked}; refreshed every poll, read by the Plugins UI to badge install state. [] until first poll.
    installed_plugins: Mapped[list] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"))
```

- [ ] **Step 4: Create the migration**

Create `backend/migrations/versions/0033_device_installed_plugins.py`:

```python
"""device_installed_plugins: per-device plugin install-state telemetry (JSONB), refreshed on poll"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "installed_plugins",
            postgresql.JSONB(),
            nullable=False,
            server_default=text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("devices", "installed_plugins")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_monitoring.py::test_device_installed_plugins_defaults_to_empty_list -q`
Expected: PASS (the test DB schema is rebuilt from the model via `create_all`, so the new column is present).

- [ ] **Step 6: Verify the prod migration applies cleanly**

Run (advances the dev `opngms` DB from 0032 → 0033):
```bash
ALEMBIC_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms" alembic upgrade head
```
Expected: `Running upgrade 0032 -> 0033, device_installed_plugins ...` with no error. Confirm head: `ALEMBIC_DATABASE_URL=... alembic current` → `0033`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/device.py backend/migrations/versions/0033_device_installed_plugins.py backend/tests/test_monitoring.py
git commit -m "feat(devices): installed_plugins JSONB column + migration 0033"
```

---

## Task 3: Persist plugin telemetry on every poll (best-effort)

**Files:**
- Modify: `backend/app/services/monitoring.py:60-70`
- Test: `backend/tests/test_monitoring.py`

After the core telemetry succeeds, fetch `get_plugin_info()` and store its `available` list. Best-effort: a failure here must NOT fail the poll or wipe the previous list.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_monitoring.py`, add `get_plugin_info` to `FakeClient` (inside the class, after `get_vpn_status`):

```python
    async def get_plugin_info(self):
        return {"product_version": "26.1.9", "plugins": ["os-wireguard"], "available": [
            {"name": "os-wireguard", "installed": True, "version": "2.6", "locked": False},
            {"name": "os-acme-client", "installed": False, "version": "4.16", "locked": False},
        ]}
```

and append a test:

```python
async def test_collect_and_store_persists_plugin_telemetry(db_engine):
    tenant_id, device_id = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, device_id)
        await collect_and_store(s, device, FakeClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        device = await s.get(Device, device_id)
        by_name = {p["name"]: p for p in device.installed_plugins}
        assert set(by_name) == {"os-wireguard", "os-acme-client"}
        assert by_name["os-wireguard"]["installed"] is True
        assert by_name["os-acme-client"]["installed"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_monitoring.py::test_collect_and_store_persists_plugin_telemetry -q`
Expected: FAIL — `device.installed_plugins == []` (the poll does not yet write it).

- [ ] **Step 3: Implement the persistence**

In `backend/app/services/monitoring.py`, in `collect_and_store`, after the firmware-version block (the `if version: device.firmware_version = version` lines) and BEFORE `await session.flush()`, add:

```python
    # Plugin inventory is best-effort: a failure must not fail the poll or wipe the last good list.
    try:
        device.installed_plugins = (await client.get_plugin_info()).get("available", [])
    except OpnsenseError:
        pass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_monitoring.py -q`
Expected: PASS (the new test + the existing `test_collect_and_store_writes_metrics_and_updates_status`, since `FakeClient` now has `get_plugin_info`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/monitoring.py backend/tests/test_monitoring.py
git commit -m "feat(monitoring): persist per-device plugin install-state on each poll"
```

---

## Task 4: Expose the telemetry — `GET /{device_id}/plugins`

**Files:**
- Modify: `backend/app/schemas/device.py`
- Modify: `backend/app/api/devices.py`
- Test: `backend/tests/test_device_plugins_api.py` (create)

A dedicated endpoint (not on `DeviceOut`, to avoid bloating the device list with ~100 plugins per row).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_device_plugins_api.py`:

```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_tenant


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid


async def test_get_device_plugins_returns_stored_telemetry(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags, installed_plugins) VALUES "
                "(:id,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}',"
                "'[{\"name\":\"os-wireguard\",\"installed\":true,\"version\":\"2.6\",\"locked\":false},"
                "{\"name\":\"os-acme-client\",\"installed\":false,\"version\":\"4.16\",\"locked\":false}]'::jsonb)"
            ),
            {"id": did, "t": tid},
        )
        await s.commit()
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/plugins")
    assert r.status_code == 200
    body = {p["name"]: p for p in r.json()}
    assert set(body) == {"os-wireguard", "os-acme-client"}
    assert body["os-wireguard"]["installed"] is True
    assert body["os-acme-client"]["version"] == "4.16"


async def test_get_device_plugins_404_for_unknown_device(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{uuid.uuid4()}/plugins")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_device_plugins_api.py -q`
Expected: FAIL — 404 on the plugins route (endpoint not defined) for the first test, so the assertion on `200` fails.

- [ ] **Step 3: Add the response schema**

In `backend/app/schemas/device.py`, add (near `DeviceOut`):

```python
class PluginInfoOut(BaseModel):
    name: str
    installed: bool
    version: str = ""
    locked: bool = False
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/api/devices.py`, update the schema import to include `PluginInfoOut`:

```python
from app.schemas.device import (
    DeviceIn, DeviceOut, DeviceUpdateIn, PluginInfoOut, RotateSecretIn, TestResultOut,
)
```

and add the endpoint after `get_device`:

```python
@router.get("/{device_id}/plugins", response_model=list[PluginInfoOut])
async def get_device_plugins(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """The plugins the box last reported (installed + available), for the Plugins UI badges."""
    device = await DeviceRepository(session, tenant_id).get(device_id)
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device.installed_plugins or []
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_device_plugins_api.py -q`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/device.py backend/app/api/devices.py backend/tests/test_device_plugins_api.py
git commit -m "feat(api): GET device/{id}/plugins exposes per-device plugin telemetry"
```

---

## Final verification (before opening the Phase 2 PR)

- [ ] **Lint:** `cd backend && ruff check app/` → `All checks passed!`
- [ ] **Targeted suites:** `cd backend && python -m pytest tests/test_opnsense_parsers.py tests/test_monitoring.py tests/test_device_plugins_api.py tests/test_capability.py tests/test_connector_plugin_info.py -q` → all pass (the `capability`/`plugin_info` suites confirm the unchanged `plugins` key didn't regress).
- [ ] **Broader regression:** `cd backend && python -m pytest tests/ -k "plugin or monitoring or device or capability" -q` → green.
- [ ] Open the PR for Phase 2; CI green; squash-merge. Then **Phase 4 (UI)** is the only remaining work — a per-device Plugins page that lists from `GET .../devices/{id}/plugins`, badges installed, links plugins-with-config-models (Phase 1 plugins catalog) to the editor, and triggers Install/Remove via the **existing** `POST .../devices/{id}/firmware/action` (`plugin_install`/`plugin_remove`).

---

## Self-review notes (author)

- **Spec coverage (Phase 2):** telemetry parse (Task 1), persistence column + migration (Task 2), poll-time write (Task 3), API exposure (Task 4). Phase 3 dropped as redundant (already implemented — see scope note).
- **Backward-compat:** `parse_plugins["plugins"]` (installed names) is byte-identical, so `capability.build_inventory` is unaffected; only an additive `available` key + a new column/endpoint.
- **Best-effort poll:** plugin-info failure is swallowed (`except OpnsenseError`) so it never fails a poll or wipes the last list; the device is already confirmed reachable by the time it runs.
- **Isolation:** the column lives on the tenant-scoped, RLS-enforced `devices` table; the endpoint is gated by `Action.DEVICE_VIEW` + the tenant context, like the sibling device GETs.
- **Type consistency:** `{name, installed, version, locked}` is identical across `parse_plugins.available` (Task 1), the stored column (Tasks 2–3), and `PluginInfoOut` (Task 4).

# Config Templates M3 — `suricata_ruleset` (IDS rulesets) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the curated `suricata_ruleset` config-template kind — a template names a set of Suricata/IDS rulesets to enable, and applying it enables them on a device and reloads the IDS engine.

**Architecture:** Reuses the existing kind-pluggable engine. A new `ids_kind.py` registers the `suricata_ruleset` template kind (→ `ids_rulesets` change_kind) plus the change applier, seeded by import side-effect in `main.py` and `worker.py` (exactly like `setting_kind.py`). The connector gains `list_ids_rulesets` (catalog read for the form) and `apply_ids_rulesets` (enable-only toggle + reconfigure, with a charset guard on each filename). A new read endpoint feeds the frontend multi-select; the form mirrors `OpnsenseSettingForm`.

**Tech Stack:** Python 3.14, FastAPI async, httpx, respx, pytest. Vite + React 19 + Mantine v9 + TanStack Query v5 + openapi-fetch + Vitest/RTL/MSW.

**API verified on real box 26.1.9 (read + revertible write):** `GET ids/settings/listRulesets` → `{rows:[{filename,description,enabled,...}]}` (68 rows, all filenames `[A-Za-z0-9._-]+`); `POST ids/settings/toggleRuleset/{filename}/{0|1}` → `{status}`; `POST ids/service/reconfigure` → `{status:"OK"}`.

---

### Task 1: Connector — `list_ids_rulesets`, `apply_ids_rulesets`, filename guard

**Files:**
- Modify: `backend/app/connectors/opnsense/client.py`
- Test: `backend/tests/test_ids_connector.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ids_connector.py`:

```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_list_ids_rulesets_returns_rows():
    respx.get(url__regex=r".*/api/ids/settings/listRulesets.*").mock(
        return_value=httpx.Response(200, json={"total": 2, "rows": [
            {"filename": "a.rules", "description": "A", "enabled": "1", "documentation": "<a>x</a>"},
            {"filename": "b.rules", "description": "B", "enabled": "0", "documentation": "<a>y</a>"},
        ]}))
    rows = await _c().list_ids_rulesets()
    assert [r["filename"] for r in rows] == ["a.rules", "b.rules"]
    assert rows[1]["enabled"] == "0"


@respx.mock
async def test_apply_ids_rulesets_enables_each_then_reconfigures():
    toggled = []
    def _cap(request):
        toggled.append(str(request.url).split("/api/")[1])
        return httpx.Response(200, json={"status": "1"})
    respx.post(url__regex=r".*/api/ids/settings/toggleRuleset/.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_ids_rulesets(
        "set", {"rulesets": ["a.rules", "b.rules"]}, dry_run=False)
    assert toggled == ["ids/settings/toggleRuleset/a.rules/1",
                       "ids/settings/toggleRuleset/b.rules/1"]
    assert rec.called and res["dry_run"] is False and res["enabled"] == ["a.rules", "b.rules"]


@respx.mock
async def test_apply_ids_rulesets_dry_run_writes_nothing():
    t = respx.post(url__regex=r".*/api/ids/settings/toggleRuleset/.*")
    res = await _c().apply_ids_rulesets("set", {"rulesets": ["a.rules"]}, dry_run=True)
    assert not t.called and res["dry_run"] is True and res["rulesets"] == ["a.rules"]


async def test_apply_ids_rulesets_rejects_bad_filename():
    with pytest.raises(ApiError):
        await _c().apply_ids_rulesets(
            "set", {"rulesets": ["../../etc/passwd"]}, dry_run=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_connector.py -v`
Expected: FAIL — `AttributeError: 'OpnsenseClient' object has no attribute 'list_ids_rulesets'`.

- [ ] **Step 3: Implement the connector methods**

In `backend/app/connectors/opnsense/client.py`, add a module-level regex next to `_PLUGIN_NAME_RE`:

```python
# IDS ruleset filenames embed in the toggleRuleset URL path: restrict to the safe charset
# (verified: all real-box ruleset filenames match this) to prevent path injection.
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")
```

Add these methods to `OpnsenseClient` (place after `apply_setting`, before `_normalize_alias_payload`):

```python
    async def list_ids_rulesets(self) -> list[dict]:
        """Catalog of installed Suricata/IDS rulesets: [{filename, description, enabled, ...}]."""
        return (await self._get("ids/settings/listRulesets")).get("rows", [])

    async def apply_ids_rulesets(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Enable the listed IDS rulesets (additive/non-destructive), then reload the engine.

        Verified against OPNsense 26.1.9: POST ids/settings/toggleRuleset/{filename}/1 enables one
        ruleset; ids/service/reconfigure reloads Suricata. Each filename is charset-validated
        (anti path-injection) before it is embedded in the URL path. dry_run performs NO mutation."""
        rulesets = list(payload.get("rulesets", []))
        if dry_run:
            return {"dry_run": True, "rulesets": rulesets}
        for name in rulesets:
            await self._post(f"ids/settings/toggleRuleset/{self._ruleset_name(name)}/1", {})
        await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "enabled": rulesets}

    @staticmethod
    def _ruleset_name(name: str) -> str:
        if not name or not _RULESET_NAME_RE.match(name):
            raise ApiError(0, f"invalid ruleset filename: {name!r}")
        return name
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_connector.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/connectors/opnsense/client.py
git add app/connectors/opnsense/client.py tests/test_ids_connector.py
git commit -m "feat(ids): connector list_ids_rulesets + enable-only apply_ids_rulesets"
```

---

### Task 2: `suricata_ruleset` template kind + `ids_rulesets` applier

**Files:**
- Create: `backend/app/services/ids_kind.py`
- Modify: `backend/app/main.py` (import side-effect), `backend/app/worker.py` (import side-effect)
- Test: `backend/tests/test_ids_kind.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ids_kind.py`:

```python
import pytest

import app.services.ids_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl


def test_suricata_ruleset_kind_registered():
    spec = tpl.TEMPLATE_KINDS["suricata_ruleset"]
    assert spec.change_kind == "ids_rulesets"
    op, target, payload = spec.to_change({"rulesets": ["a.rules"]})
    assert op == "set" and target == "ids_rulesets" and payload["rulesets"] == ["a.rules"]


def test_validate_accepts_good_list():
    tpl.validate_body("suricata_ruleset", {"rulesets": ["abuse.ch.urlhaus.rules", "et.rules"]})


@pytest.mark.parametrize("body", [
    {"rulesets": []},                       # empty
    {"rulesets": "a.rules"},                # not a list
    {"rulesets": [123]},                    # not strings
    {"rulesets": ["../etc/passwd"]},        # bad charset
    {},                                     # missing
])
def test_validate_rejects_bad(body):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("suricata_ruleset", body)


async def test_applier_dispatches_to_apply_ids_rulesets():
    calls = {}

    class FakeClient:
        async def apply_ids_rulesets(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "enabled": payload["rulesets"]}

    await ca.apply_for_kind(
        FakeClient(), "ids_rulesets", "set", {"rulesets": ["a.rules"]}, dry_run=False)
    operation, payload, dry = calls["args"]
    assert operation == "set" and payload == {"rulesets": ["a.rules"]} and dry is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_kind.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ids_kind'`.

- [ ] **Step 3: Implement the kind module**

Create `backend/app/services/ids_kind.py`:

```python
"""Register the curated `suricata_ruleset` template kind + its config-change applier.

A template body is `{"rulesets": [filename, ...]}` — the IDS rulesets to ENABLE. Apply is
additive/non-destructive (it enables the listed rulesets; it never disables others)."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

# Mirrors the connector's URL-path charset guard (anti path-injection); validated server-side too.
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def _validate(body: dict) -> None:
    body = body or {}
    rulesets = body.get("rulesets")
    if not isinstance(rulesets, list) or not rulesets:
        raise InvalidTemplateError("'rulesets' must be a non-empty list")
    for name in rulesets:
        if not isinstance(name, str) or not _RULESET_NAME_RE.match(name):
            raise InvalidTemplateError(f"invalid ruleset filename: {name!r}")


register_template_kind("suricata_ruleset", TemplateKind(
    validate=_validate,
    change_kind="ids_rulesets",
    to_change=lambda body: ("set", "ids_rulesets", body),
    pinned=(),                                   # no identity field; override replaces the whole list
))


async def _apply_ids_rulesets(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_ids_rulesets(operation, payload, dry_run=dry_run)


register_change_applier("ids_rulesets", _apply_ids_rulesets)
```

- [ ] **Step 4: Register the module for import side-effect in both processes**

In `backend/app/main.py`, next to the existing setting_kind import (line 6), add:

```python
import app.services.ids_kind  # noqa: F401  — registers suricata_ruleset kind at API-process startup
```

In `backend/app/worker.py`, next to the existing setting_kind import (line 9), add:

```python
import app.services.ids_kind  # noqa: F401  — registers suricata_ruleset kind at worker-process startup
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_kind.py -v`
Expected: PASS (8 passed — 1 + 1 + 5 parametrized + 1).

- [ ] **Step 6: Commit**

```bash
cd backend && .venv/bin/ruff check app/services/ids_kind.py app/main.py app/worker.py
git add app/services/ids_kind.py app/main.py app/worker.py tests/test_ids_kind.py
git commit -m "feat(ids): register suricata_ruleset template kind + ids_rulesets applier"
```

---

### Task 3: Read endpoint — `GET .../opnsense/ids/rulesets`

**Files:**
- Create: `backend/app/api/ids.py`
- Modify: `backend/app/main.py` (include router)
- Test: `backend/tests/test_ids_api.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ids_api.py`:

```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.factories import make_membership, make_tenant, make_user

_RULESETS = [
    {"filename": "abuse.ch.urlhaus.rules", "description": "abuse.ch/URLhaus", "enabled": "0",
     "documentation": "<a href='x'>x</a>", "documentation_url": "x"},
    {"filename": "OPNsense.rules", "description": "OPNsense", "enabled": "1",
     "documentation": "<a href='y'>y</a>", "documentation_url": "y"},
]


async def _seed_members(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        admin = await make_user(s, email="ta@x.io", password="pw12345")
        await make_membership(s, user_id=admin.id, tenant_id=t.id, role="tenant_admin")
        await s.commit()
        return t.id


async def _insert_device(db_engine, tenant_id, name="fw1"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name},
        )
        await s.commit()
    return did


async def _login(api_client, email):
    await api_client.post("/api/login", json={"email": email, "password": "pw12345"})


async def test_list_rulesets_returns_trimmed_catalog(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine)
    did = await _insert_device(db_engine, tid)

    async def _stub(self):
        return _RULESETS

    monkeypatch.setattr(
        "app.connectors.opnsense.client.OpnsenseClient.list_ids_rulesets", _stub)
    monkeypatch.setattr("app.core.crypto.decrypt", lambda blob: "x")

    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/ids/rulesets")
    assert r.status_code == 200
    body = r.json()
    assert {e["filename"] for e in body} == {"abuse.ch.urlhaus.rules", "OPNsense.rules"}
    # trimmed to what the form needs (no documentation HTML)
    assert set(body[0].keys()) == {"filename", "description", "enabled"}


async def test_list_rulesets_cross_tenant_device_is_404(api_client, db_engine):
    tid = await _seed_members(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other")
        await s.commit()
        other_tid = other.id
    did = await _insert_device(db_engine, other_tid, name="otherfw")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/ids/rulesets")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_api.py -v`
Expected: FAIL — 404 for an unmatched route (the endpoint does not exist yet).

- [ ] **Step 3: Implement the read endpoint**

Create `backend/app/api/ids.py`:

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.device import Device

router = APIRouter(prefix="/api", tags=["ids"])


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/ids/rulesets")
async def list_ids_rulesets(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """The device's installed IDS rulesets, trimmed to {filename, description, enabled} for the form."""
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = OpnsenseClient(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
    )
    try:
        rows = await client.list_ids_rulesets()
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return [
        {"filename": r.get("filename"), "description": r.get("description"), "enabled": r.get("enabled")}
        for r in rows
    ]
```

- [ ] **Step 4: Register the router in main.py**

In `backend/app/main.py`, add the import (with the other `app.api.*` imports) and include it after `settings_router`:

```python
from app.api.ids import router as ids_router
```
```python
app.include_router(ids_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_ids_api.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full backend suite + ruff**

Run: `cd backend && .venv/bin/python -m pytest -q && .venv/bin/ruff check app tests`
Expected: all pass, ruff clean.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/api/ids.py app/main.py tests/test_ids_api.py
git commit -m "feat(ids): GET rulesets catalog endpoint for the template form"
```

---

### Task 4: Frontend — `useIdsRulesets` hook + `IdsRulesetForm`

**Files:**
- Modify: `frontend/src/templates/settingHooks.ts` (add `useIdsRulesets`)
- Create: `frontend/src/templates/IdsRulesetForm.tsx`
- Modify: `frontend/src/i18n/en.ts` (add `templates.ids.*`)
- Test: `frontend/src/templates/IdsRulesetForm.test.tsx`

- [ ] **Step 1: Add i18n strings**

In `frontend/src/i18n/en.ts`, immediately after the `setting: { ... }` block (after line 154's closing `},`), add:

```typescript
    kindIdsRulesets: "Suricata/IDS rulesets",
    ids: {
      referenceDevice: "Reference device (to read the available rulesets)",
      load: "Load rulesets",
      loadHint: "Pick a device, then load its rulesets to choose which to enable.",
      noDevice: "No device available in the active tenant to read rulesets from.",
      loadFailed: "Could not read the rulesets from the device.",
      rulesets: "Rulesets to enable",
      noRulesets: "No rulesets found on the device.",
      note: "Applying enables the selected rulesets (additive — it does not disable others).",
    },
```

(Place `kindIdsRulesets` and `ids` as siblings of `kindSetting`/`setting`, inside the `templates` object.)

- [ ] **Step 2: Add the data hook**

In `frontend/src/templates/settingHooks.ts`, add a `RulesetRow` type and `useIdsRulesets` hook (reuses `useTenant`; mutation triggered by the Load button, like `useIntrospectSetting`):

```typescript
export type RulesetRow = { filename: string; description: string; enabled: string };

export function useIdsRulesets(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (): Promise<RulesetRow[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/opnsense/ids/rulesets",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.templates.ids.loadFailed);
      return data as RulesetRow[];
    },
  });
}
```

- [ ] **Step 3: Write the failing test**

Create `frontend/src/templates/IdsRulesetForm.test.tsx`:

```typescript
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { IdsRulesetForm } from "./IdsRulesetForm";

vi.mock("../tenant/useTenant", () => ({ useTenant: () => ({ activeId: "t1" }) }));

const server = setupServer(
  http.get("/api/tenants/t1/devices", () =>
    HttpResponse.json([{ id: "d1", name: "fw1" }])),
  http.get("/api/tenants/t1/devices/d1/opnsense/ids/rulesets", () =>
    HttpResponse.json([
      { filename: "a.rules", description: "Alpha", enabled: "0" },
      { filename: "b.rules", description: "Bravo", enabled: "1" },
    ])),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><MantineProvider>{ui}</MantineProvider></QueryClientProvider>,
  );
}

test("loads rulesets and reports selection", async () => {
  const onChange = vi.fn();
  wrap(<IdsRulesetForm value={{ rulesets: [] }} onChange={onChange} />);

  // pick the device
  fireEvent.click(await screen.findByTestId("ids-device"));
  fireEvent.click(await screen.findByText("fw1"));
  // load
  fireEvent.click(screen.getByTestId("ids-load"));

  // the multi-select appears with the catalog
  await waitFor(() => expect(screen.getByTestId("ids-rulesets")).toBeInTheDocument());
  fireEvent.click(screen.getByTestId("ids-rulesets"));
  fireEvent.click(await screen.findByText("Alpha"));
  await waitFor(() =>
    expect(onChange).toHaveBeenCalledWith({ rulesets: ["a.rules"] }));
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/templates/IdsRulesetForm.test.tsx`
Expected: FAIL — cannot resolve `./IdsRulesetForm`.

- [ ] **Step 5: Implement the form**

Create `frontend/src/templates/IdsRulesetForm.tsx`:

```typescript
import { Button, Group, MultiSelect, Select, Stack, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import { type RulesetRow, useIdsRulesets, useTenantDevices } from "./settingHooks";

type IdsBody = { rulesets: string[] };

export function IdsRulesetForm(
  { value, onChange }: { value: IdsBody; onChange: (v: IdsBody) => void },
) {
  const t = useT();
  const { data: devices } = useTenantDevices();
  const [deviceId, setDeviceId] = useState<string>("");
  const [rows, setRows] = useState<RulesetRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const load = useIdsRulesets(deviceId);

  const deviceData = (devices ?? []).map((d) => ({ value: d.id, label: d.name }));
  const rulesetData = rows.map((r) => ({ value: r.filename, label: r.description || r.filename }));

  async function loadRulesets() {
    try {
      const res = await load.mutateAsync();
      setRows(res);
      setLoaded(true);
    } catch {
      setRows([]);
      setLoaded(false);
      notifications.show({ color: "red", message: t.templates.ids.loadFailed });
    }
  }

  return (
    <Stack>
      {deviceData.length === 0
        ? <Text size="sm" c="dimmed" data-testid="ids-no-device">{t.templates.ids.noDevice}</Text>
        : (
          <>
            <Select
              label={t.templates.ids.referenceDevice}
              data={deviceData}
              data-testid="ids-device"
              value={deviceId || null}
              onChange={(id) => setDeviceId(id ?? "")}
            />
            <Group>
              <Button
                data-testid="ids-load"
                onClick={loadRulesets}
                loading={load.isPending}
                disabled={!deviceId}
              >
                {t.templates.ids.load}
              </Button>
            </Group>
          </>
        )}

      {!loaded
        ? <Text size="sm" c="dimmed" data-testid="ids-load-hint">{t.templates.ids.loadHint}</Text>
        : rows.length === 0
          ? <Text size="sm" c="dimmed" data-testid="ids-no-rulesets">{t.templates.ids.noRulesets}</Text>
          : (
            <MultiSelect
              label={t.templates.ids.rulesets}
              data={rulesetData}
              data-testid="ids-rulesets"
              searchable
              value={value.rulesets}
              onChange={(sel) => onChange({ rulesets: sel })}
            />
          )}

      <Text size="xs" c="dimmed" data-testid="ids-note">{t.templates.ids.note}</Text>
    </Stack>
  );
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/templates/IdsRulesetForm.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd frontend
git add src/templates/IdsRulesetForm.tsx src/templates/IdsRulesetForm.test.tsx src/templates/settingHooks.ts src/i18n/en.ts
git commit -m "feat(ids): IdsRulesetForm + useIdsRulesets hook + i18n"
```

---

### Task 5: Frontend — wire `suricata_ruleset` into `TemplateFormModal`

**Files:**
- Modify: `frontend/src/templates/TemplateFormModal.tsx`
- Test: `frontend/src/templates/TemplateFormModal.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/templates/TemplateFormModal.test.tsx` a test that selects the new kind, loads rulesets, picks one, saves, and asserts the create payload is `{kind:"suricata_ruleset", name, body:{rulesets:["a.rules"]}}`. Match the file's existing harness (provider wrappers, MSW handlers, `useTenant` mock). Model it on the existing `opnsense_setting` test in this file: change the kind Select to `t.templates.kindIdsRulesets`, drive `ids-device`/`ids-load`/`ids-rulesets`, then click `tpl-save` and assert the POST body via the MSW handler that captures `/api/templates`.

```typescript
test("creates a suricata_ruleset template", async () => {
  let captured: unknown = null;
  server.use(
    http.get("/api/tenants/t1/devices", () => HttpResponse.json([{ id: "d1", name: "fw1" }])),
    http.get("/api/tenants/t1/devices/d1/opnsense/ids/rulesets", () =>
      HttpResponse.json([{ filename: "a.rules", description: "Alpha", enabled: "0" }])),
    http.post("/api/templates", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json({ id: "x", kind: "suricata_ruleset", name: "n", version: 1 }, { status: 201 });
    }),
  );
  renderModal();  // however this file renders the modal opened, editing=null

  fireEvent.change(screen.getByTestId("tpl-name"), { target: { value: "Baseline IDS" } });
  // switch kind
  fireEvent.click(screen.getByTestId("tpl-kind"));
  fireEvent.click(await screen.findByText("Suricata/IDS rulesets"));
  // device + load + pick
  fireEvent.click(await screen.findByTestId("ids-device"));
  fireEvent.click(await screen.findByText("fw1"));
  fireEvent.click(screen.getByTestId("ids-load"));
  fireEvent.click(await screen.findByTestId("ids-rulesets"));
  fireEvent.click(await screen.findByText("Alpha"));
  // save
  fireEvent.click(screen.getByTestId("tpl-save"));
  await waitFor(() => expect(captured).toEqual({
    kind: "suricata_ruleset", name: "Baseline IDS", description: "",
    body: { rulesets: ["a.rules"] },
  }));
});
```

If the existing file lacks `renderModal`/handler helpers, reuse whatever the `opnsense_setting` test already uses; keep the assertion on the captured POST body.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/templates/TemplateFormModal.test.tsx`
Expected: FAIL — the kind option "Suricata/IDS rulesets" is not in the Select; `ids-device` not found.

- [ ] **Step 3: Wire the modal**

In `frontend/src/templates/TemplateFormModal.tsx`:

1. Import the form and add a body state next to `settingBody`:

```typescript
import { IdsRulesetForm } from "./IdsRulesetForm";
```
```typescript
type IdsBody = { rulesets: string[] };
const EMPTY_IDS: IdsBody = { rulesets: [] };
```
```typescript
  const [idsBody, setIdsBody] = useState<IdsBody>(EMPTY_IDS);
```

2. In the `useEffect` (the open/reset block), seed `idsBody` from `editing` when its kind is `suricata_ruleset`:

```typescript
      setIdsBody(editing?.kind === "suricata_ruleset"
        ? ((editing.body as IdsBody | undefined) ?? EMPTY_IDS)
        : EMPTY_IDS);
```

3. Add the kind option to the Select `data` array:

```typescript
              { value: "suricata_ruleset", label: t.templates.kindIdsRulesets },
```

4. Add a conditional render branch. Change the body render so it is:

```typescript
          {kind === "opnsense_setting"
            ? <OpnsenseSettingForm value={settingBody} onChange={setSettingBody} />
            : kind === "suricata_ruleset"
            ? <IdsRulesetForm value={idsBody} onChange={setIdsBody} />
            : (
              <>
                {/* existing alias fields (type + content) unchanged */}
              </>
            )}
```

5. In `submit`, add a `suricata_ruleset` branch before the alias `else`:

```typescript
      } else if (kind === "suricata_ruleset") {
        if (editing) {
          await update.mutateAsync({ id: editing.id,
            body: { name: v.name, description: v.description, body: idsBody } });
          notifications.show({ message: t.templates.updated });
        } else {
          await create.mutateAsync({ kind: "suricata_ruleset", name: v.name,
            description: v.description, body: idsBody });
          notifications.show({ message: t.templates.created });
        }
      } else {
```

(Convert the current `if (kind === "opnsense_setting") { ... } else { ...alias... }` into `if (opnsense_setting) {...} else if (suricata_ruleset) {...} else {...alias...}`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/templates/TemplateFormModal.test.tsx`
Expected: PASS.

- [ ] **Step 5: Run the full frontend suite + lint**

Run: `cd frontend && npx vitest run && npm run lint`
Expected: all pass, lint clean.

- [ ] **Step 6: Commit**

```bash
cd frontend
git add src/templates/TemplateFormModal.tsx src/templates/TemplateFormModal.test.tsx
git commit -m "feat(ids): wire suricata_ruleset kind into the template form modal"
```

---

### Task 6: Regen API types, full suites, live verify

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (generated)

- [ ] **Step 1: Regenerate the typed API client**

The backend gained `GET /api/tenants/{tid}/devices/{did}/opnsense/ids/rulesets`. Regenerate so `api.GET(...)` is typed (Task 4's hook currently relies on the new path):

Run (backend must be importable for the OpenAPI dump; follow the repo's existing gen script):
```bash
cd frontend && npm run gen:api
```
Then re-run the frontend suite to confirm types still compile:
```bash
cd frontend && npx vitest run && npm run lint && npx tsc --noEmit
```
Expected: all green.

- [ ] **Step 2: Commit the regenerated schema (if changed)**

```bash
cd frontend
git add src/api/schema.d.ts
git commit -m "chore(api): regen client types for ids/rulesets endpoint"
```

- [ ] **Step 3: Live verify on the real box (revertible)**

With the dev stack pointed at device 192.168.1.82 (or via an ephemeral connector probe using the verified endpoints), exercise the end-to-end path against a currently-disabled ruleset (e.g. `abuse.ch.feodotracker.rules`):
1. Create a `suricata_ruleset` template `{rulesets: ["abuse.ch.feodotracker.rules"]}`.
2. Apply it to the device (real, not dry-run).
3. Confirm via `GET ids/settings/listRulesets` that the ruleset's `enabled == "1"`.
4. **Revert:** `POST ids/settings/toggleRuleset/abuse.ch.feodotracker.rules/0` + `ids/service/reconfigure`; confirm `enabled == "0"`.

Document the verification result in the PR description. Never print API secrets; use the ephemeral `/tmp` probe pattern with `verify_tls=False`.

- [ ] **Step 4: Final full suites**

Run: `cd backend && .venv/bin/python -m pytest -q && .venv/bin/ruff check app tests`
Run: `cd frontend && npx vitest run && npm run lint`
Expected: all green.

---

## Self-Review notes

- **Spec coverage:** connector read+write (Task 1) · kind+applier registered in both processes (Task 2) · catalog read endpoint (Task 3) · form + hook + i18n (Task 4) · modal wiring (Task 5) · type regen + live verify (Task 6). Preview needs no change (already kind-aware via `TEMPLATE_KINDS[kind].to_change`).
- **Charset guard** is enforced in BOTH the connector (`_ruleset_name`) and the kind validator (`_validate`) — defense in depth against path injection.
- **Enable-only/additive** semantics documented in the spec, the connector docstring, the kind module docstring, and surfaced to the user via `templates.ids.note`.
- **Type consistency:** body shape `{rulesets: string[]}`, change_kind `"ids_rulesets"`, target `"ids_rulesets"`, applier signature `(client, operation, payload, *, dry_run)` are consistent across all tasks.

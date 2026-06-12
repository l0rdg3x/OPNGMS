# Syslog Phase 3.1 — Log-forwarding Provisioning UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator enable/disable a device's log forwarding from the device page and see whether logs are actually flowing (cert expiry + a "last log received" liveness indicator).

**Architecture:** Additive on the merged Phase-1 provisioning API and Phase-2 OpenSearch client. The backend stores the issued cert's expiry, resolves a best-effort "last log received" timestamp (a `size=1` OpenSearch query, tenant+device filtered), and returns both on the existing status endpoint. A React `LogForwardingCard` on `DeviceDetailPage` drives `GET/POST .../log-forwarding[/enable|/disable]`.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy + Alembic · httpx · OpenSearch · React 19 + Mantine v9 + @tanstack/react-query + openapi-fetch · pytest + respx · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-phase3a-provisioning-ux-design.md`
**Branch:** `feat/log-forwarding-provisioning-ux` (already created off main).

---

## File Structure

**Backend — create:** migration `backend/migrations/versions/0025_log_forwarding_cert_expiry.py`; tests `tests/test_cert_not_after.py`, `tests/test_latest_log_at.py`, `tests/test_log_forwarding_status.py`.
**Backend — modify:** `app/services/syslog_ca.py` (`cert_not_after` helper), `app/models/device_log_forwarding.py` (column), `app/services/log_forwarding.py` (store expiry), `app/services/log_search.py` (`latest_log_at`), `app/schemas/log_forwarding.py` (2 fields), `app/api/log_forwarding.py` (wire liveness).
**Frontend — create:** `frontend/src/logs/logForwardingHooks.ts`, `frontend/src/components/LogForwardingCard.tsx`, `frontend/src/components/__tests__/logForwarding.test.tsx`.
**Frontend — modify:** `frontend/src/api/schema.d.ts` + `frontend/openapi.json` (regen), `frontend/src/pages/DeviceDetailPage.tsx` (new tab), `frontend/src/i18n/en.ts` (tab/label strings).

---

## Conventions
- Backend DB tests prefix: `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`. Pure/respx tests always run (asyncio_mode=auto; no decorator needed).
- Run from repo root; `git add` with `backend/...`/`frontend/...` paths from the repo root (a `cd backend` leaves cwd there and breaks `backend/...` adds).
- Frontend: before the PR run `npm run build` (tsc -b + vite). English everywhere; commit after each task.

---

# PHASE A — backend

## Task 1: Capture the device cert expiry

**Files:**
- Modify: `backend/app/services/syslog_ca.py`, `backend/app/models/device_log_forwarding.py`, `backend/app/services/log_forwarding.py`
- Create: `backend/migrations/versions/0025_log_forwarding_cert_expiry.py`, `backend/tests/test_cert_not_after.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_cert_not_after.py`

```python
from datetime import UTC, datetime

from app.services.syslog_ca import build_ca, cert_not_after, issue_device_cert


def test_cert_not_after_is_aware_and_future():
    ca_cert, ca_key = build_ca()
    cert_pem, _ = issue_device_cert(ca_cert, ca_key, tenant_id="t1", device_id="d1")
    exp = cert_not_after(cert_pem)
    assert isinstance(exp, datetime)
    assert exp.tzinfo is not None          # aware
    assert exp > datetime.now(UTC)         # not already expired
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_cert_not_after.py -v`
Expected: FAIL (ImportError: cannot import name `cert_not_after`).

- [ ] **Step 3: Add the helper** to `backend/app/services/syslog_ca.py`

At the top, ensure `from datetime import datetime` is imported (add it if missing — the file already imports `x509`/`hashes`). Add this function next to `cert_serial_and_fingerprint`:

```python
def cert_not_after(cert_pem: bytes) -> datetime:
    """The certificate's expiry as an aware UTC datetime."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.not_valid_after_utc
```

- [ ] **Step 4: Add the column to the model** — `backend/app/models/device_log_forwarding.py`

After the `provisioned_at` column add:

```python
    cert_not_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

(`datetime`, `Mapped`, `mapped_column`, `DateTime` are already imported in that file.)

- [ ] **Step 5: Create migration 0025** — `backend/migrations/versions/0025_log_forwarding_cert_expiry.py`

```python
"""device_log_forwarding.cert_not_after (cert expiry for the provisioning UX)"""
import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_log_forwarding",
        sa.Column("cert_not_after", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_log_forwarding", "cert_not_after")
```

- [ ] **Step 6: Store the expiry at provision time** — `backend/app/services/log_forwarding.py`

Add `cert_not_after` to the import from `app.services.syslog_ca` (the file already imports `cert_serial_and_fingerprint` from there). In `provision_device`, right after `serial, fp = cert_serial_and_fingerprint(cert_pem)` add:

```python
    not_after = cert_not_after(cert_pem)
```

and after `row.cert_serial, row.cert_fingerprint = serial, fp` add:

```python
    row.cert_not_after = not_after
```

- [ ] **Step 7: Run to verify pass + migration applies**

Run: `cd backend && .venv/bin/pytest tests/test_cert_not_after.py -v` → PASS.
Run: `cd backend && ADMIN_DATABASE_URL="postgresql+psycopg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/alembic upgrade head` → succeeds (column added). If the test harness builds the schema from migrations, this also keeps `tests/test_log_forwarding_status.py` (Task 3) green.
Run: `cd backend && .venv/bin/ruff check app/services/syslog_ca.py app/services/log_forwarding.py app/models/device_log_forwarding.py` → clean.

> If `alembic upgrade head` reports the DB is already at an older head and won't move, that's fine for local — the conftest recreates the test DB per the project's existing fixture; just ensure both the **model column** and the **migration** exist so metadata-based and alembic-based setups agree.

- [ ] **Step 8: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/syslog_ca.py backend/app/models/device_log_forwarding.py backend/app/services/log_forwarding.py backend/migrations/versions/0025_log_forwarding_cert_expiry.py backend/tests/test_cert_not_after.py
git commit -m "feat(log-forwarding): capture device cert expiry (cert_not_after)"
```

---

## Task 2: Liveness helper `latest_log_at`

**Files:**
- Modify: `backend/app/services/log_search.py`
- Test: `backend/tests/test_latest_log_at.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_latest_log_at.py`

```python
import uuid

import httpx
import respx

from app.services.log_search import latest_log_at


class _S:
    opensearch_url = "http://opensearch:9200"


_URL = "http://opensearch:9200/opngms-logs-*/_search"


@respx.mock
async def test_latest_log_at_returns_timestamp():
    respx.post(_URL).mock(return_value=httpx.Response(200, json={
        "hits": {"hits": [{"_source": {"@timestamp": "2026-06-01T10:00:00Z"}}]}}))
    out = await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4())
    assert out is not None
    assert out.year == 2026 and out.month == 6 and out.day == 1
    assert out.tzinfo is not None


@respx.mock
async def test_latest_log_at_none_on_empty():
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"hits": {"hits": []}}))
    assert await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4()) is None


@respx.mock
async def test_latest_log_at_none_on_error():
    respx.post(_URL).mock(return_value=httpx.Response(503, json={}))
    assert await latest_log_at(_S(), tenant_id=uuid.uuid4(), device_id=uuid.uuid4()) is None
```

- [ ] **Step 2: Run to verify it fails** (ImportError).

- [ ] **Step 3: Append `latest_log_at` to `backend/app/services/log_search.py`**

(The module already imports `uuid`, `httpx`, and `from datetime import datetime`.)

```python
async def latest_log_at(settings, *, tenant_id: uuid.UUID, device_id: uuid.UUID) -> datetime | None:
    """Best-effort @timestamp of the most recent log for this device, or None if there are no logs
    or OpenSearch is unreachable. Keeps the mandatory tenant filter (same guarantee as search_logs)."""
    body = {
        "query": {"bool": {"filter": [
            {"term": {"tenant_id": str(tenant_id)}},
            {"term": {"device_id": str(device_id)}},
        ]}},
        "sort": [{"@timestamp": "desc"}],
        "size": 1,
        "_source": ["@timestamp"],
    }
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None
        ts = (hits[0].get("_source", {}) or {}).get("@timestamp")
        if not ts:
            return None
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (httpx.HTTPError, ValueError, KeyError):
        return None
```

- [ ] **Step 4: Run to verify pass** (3 passed) + `.venv/bin/ruff check app/services/log_search.py` clean.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/log_search.py backend/tests/test_latest_log_at.py
git commit -m "feat(log-forwarding): best-effort latest_log_at liveness helper"
```

---

## Task 3: Status response carries cert expiry + liveness

**Files:**
- Modify: `backend/app/schemas/log_forwarding.py`, `backend/app/api/log_forwarding.py`
- Test: `backend/tests/test_log_forwarding_status.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_log_forwarding_status.py`

```python
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_membership, make_user


async def _seed(db_engine, *, enabled: bool):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,cert_not_after) "
            "VALUES (:d,:t,:e,'ab','cd',:na)"),
            {"d": did, "t": tid, "e": enabled, "na": datetime(2027, 1, 1, tzinfo=UTC)})
        await s.commit()
    return tid, did


async def _login(api_client, email="op@x.io"):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200


async def test_status_includes_liveness_when_enabled(api_client, db_engine, monkeypatch):
    called = {"n": 0}

    async def fake_latest(settings, *, tenant_id, device_id):
        called["n"] += 1
        return datetime(2026, 6, 1, 10, 0, tzinfo=UTC)

    monkeypatch.setattr("app.api.log_forwarding.latest_log_at", fake_latest)
    tid, did = await _seed(db_engine, enabled=True)
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["cert_not_after"].startswith("2027-01-01")
    assert body["last_log_at"].startswith("2026-06-01")
    assert called["n"] == 1


async def test_status_skips_opensearch_when_disabled(api_client, db_engine, monkeypatch):
    called = {"n": 0}

    async def fake_latest(settings, *, tenant_id, device_id):
        called["n"] += 1
        return datetime(2026, 6, 1, tzinfo=UTC)

    monkeypatch.setattr("app.api.log_forwarding.latest_log_at", fake_latest)
    tid, did = await _seed(db_engine, enabled=False)
    await _login(api_client)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert r.status_code == 200, r.text
    assert r.json()["last_log_at"] is None
    assert called["n"] == 0          # no OpenSearch round-trip when disabled
```

- [ ] **Step 2: Run to verify it fails** (KeyError on `cert_not_after`/`last_log_at`, or AttributeError).

- [ ] **Step 3: Extend the schema** — `backend/app/schemas/log_forwarding.py`

Add two optional fields to `LogForwardingOut`:

```python
    cert_not_after: datetime | None = None
    last_log_at: datetime | None = None
```

- [ ] **Step 4: Map cert_not_after + wire liveness** — `backend/app/api/log_forwarding.py`

Add the import near the other service imports:

```python
from app.services.log_search import latest_log_at
```

In `_out`, set `cert_not_after` from the row (the `None` branch already returns the disabled default — extend the populated branch):

```python
def _out(row, *, device_id: uuid.UUID) -> LogForwardingOut:
    if row is None:
        return LogForwardingOut(device_id=device_id, enabled=False, cert_serial="",
                                cert_fingerprint="", provisioned_at=None)
    return LogForwardingOut(device_id=row.device_id, enabled=row.enabled, cert_serial=row.cert_serial,
                            cert_fingerprint=row.cert_fingerprint, provisioned_at=row.provisioned_at,
                            cert_not_after=row.cert_not_after)
```

Replace the body of `status_log_forwarding` so it resolves liveness only when enabled:

```python
@router.get("", response_model=LogForwardingOut)
async def status_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    await _device(session, tenant_id, device_id)
    row = await DeviceLogForwardingRepository(session, tenant_id).get(device_id)
    out = _out(row, device_id=device_id)
    if row is not None and row.enabled:
        out.last_log_at = await latest_log_at(get_settings(), tenant_id=tenant_id, device_id=device_id)
    return out
```

(`get_settings` is already imported in this file.)

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_forwarding_status.py -v`
Expected: 2 passed. `.venv/bin/ruff check app/api/log_forwarding.py app/schemas/log_forwarding.py` clean.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/log_forwarding.py backend/app/api/log_forwarding.py backend/tests/test_log_forwarding_status.py
git commit -m "feat(log-forwarding): status returns cert expiry + liveness (last_log_at)"
```

---

# PHASE B — frontend

## Task 4: `LogForwardingCard` on the device page

**Files:**
- Modify: `frontend/src/api/schema.d.ts` + `frontend/openapi.json` (regen), `frontend/src/pages/DeviceDetailPage.tsx`, `frontend/src/i18n/en.ts`
- Create: `frontend/src/logs/logForwardingHooks.ts`, `frontend/src/components/LogForwardingCard.tsx`, `frontend/src/components/__tests__/logForwarding.test.tsx`

- [ ] **Step 1: Regenerate the OpenAPI client**

Run: `cd frontend && npm run gen:api` then `grep -c "last_log_at" src/api/schema.d.ts` → > 0.

- [ ] **Step 2: Write the failing test** — `frontend/src/components/__tests__/logForwarding.test.tsx`

(Mirror the local `withTenant` + MSW pattern from `src/pages/__tests__/logs.test.tsx`; fix the base URL / `TenantContext` import to match that file exactly.)

```tsx
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogForwardingCard } from "../LogForwardingCard";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "operator") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const STATUS = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding";
const ENABLE = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding/enable";

describe("LogForwardingCard", () => {
  it("shows enabled status + liveness and enables on confirm", async () => {
    let enabled = false;
    server.use(
      http.get(STATUS, () => HttpResponse.json({
        device_id: "d1", enabled, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
        provisioned_at: enabled ? "2026-06-01T00:00:00Z" : null,
        cert_not_after: enabled ? "2027-01-01T00:00:00Z" : null,
        last_log_at: enabled ? new Date().toISOString() : null,
      })),
      http.post(ENABLE, () => { enabled = true; return HttpResponse.json({
        device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
        provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z", last_log_at: null }); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    expect(await screen.findByTestId("lf-status")).toHaveTextContent(/disabled/i);
    await userEvent.click(screen.getByTestId("lf-enable"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(screen.getByTestId("lf-status")).toHaveTextContent(/enabled/i));
    expect(screen.getByTestId("lf-liveness")).toBeInTheDocument();
  });

  it("hides action buttons for read_only", async () => {
    server.use(http.get(STATUS, () => HttpResponse.json({
      device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
      provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z",
      last_log_at: "2026-06-01T10:00:00Z" })));
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "read_only"));
    expect(await screen.findByTestId("lf-status")).toBeInTheDocument();
    expect(screen.queryByTestId("lf-enable")).toBeNull();
    expect(screen.queryByTestId("lf-disable")).toBeNull();
  });
});
```

> The confirm button testid (`confirm-ok`) must match `ConfirmModal`'s confirm button. Open `src/components/ConfirmModal.tsx` and use its actual confirm `data-testid` (it is `confirm-ok` if present; otherwise use the one defined there and update the test).

- [ ] **Step 3: Run to verify it fails** (`cd frontend && npm test -- logForwarding`).

- [ ] **Step 4: Hooks** — `frontend/src/logs/logForwardingHooks.ts`

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type LogForwardingOut = components["schemas"]["LogForwardingOut"];

const PATH = "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding" as const;

export function useLogForwardingStatus(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["log-forwarding", activeId, deviceId],
    queryFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.GET(PATH, {
        params: { path: { tenant_id: activeId!, device_id: deviceId } },
      });
      if (error || !data) throw new Error("status failed");
      return data;
    },
  });
}

function useLfMutation(deviceId: string, action: "enable" | "disable") {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.POST(`${PATH}/${action}` as typeof PATH, {
        params: { path: { tenant_id: activeId!, device_id: deviceId } },
      });
      if (error || !data) throw new Error(`${action} failed`);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["log-forwarding", activeId, deviceId] }),
  });
}

export const useEnableLogForwarding = (deviceId: string) => useLfMutation(deviceId, "enable");
export const useDisableLogForwarding = (deviceId: string) => useLfMutation(deviceId, "disable");
```

> If openapi-fetch rejects the templated `` `${PATH}/${action}` `` union, replace it with two explicit string literals (`"/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/enable"` and `.../disable`) selected by `action`. Make `tsc -b` pass.

- [ ] **Step 5: Card** — `frontend/src/components/LogForwardingCard.tsx`

```tsx
import { useState } from "react";
import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, Title, Tooltip } from "@mantine/core";
import dayjs from "dayjs";

import { useTenant } from "../tenant/useTenant";
import {
  useDisableLogForwarding, useEnableLogForwarding, useLogForwardingStatus,
} from "../logs/logForwardingHooks";
import { ConfirmModal } from "./ConfirmModal";

function liveness(lastLogAt: string | null | undefined): { color: string; label: string } {
  if (!lastLogAt) return { color: "gray", label: "no logs yet" };
  const mins = dayjs().diff(dayjs(lastLogAt), "minute");
  if (mins <= 15) return { color: "green", label: `active (${dayjs(lastLogAt).fromNow()})` };
  if (mins <= 60 * 24) return { color: "yellow", label: `quiet (${dayjs(lastLogAt).fromNow()})` };
  return { color: "gray", label: `stale (${dayjs(lastLogAt).fromNow()})` };
}

export function LogForwardingCard({ deviceId }: { deviceId: string }) {
  const { activeId, tenants } = useTenant();
  const role = tenants.find((tn) => tn.id === activeId)?.role ?? null;
  const canWrite = role === "tenant_admin" || role === "operator";
  const status = useLogForwardingStatus(deviceId);
  const enable = useEnableLogForwarding(deviceId);
  const disable = useDisableLogForwarding(deviceId);
  const [confirm, setConfirm] = useState<null | "enable" | "disable">(null);

  if (status.isLoading) return <Loader />;
  const s = status.data;
  const enabled = s?.enabled ?? false;
  const live = liveness(s?.last_log_at);
  const expiry = s?.cert_not_after ? dayjs(s.cert_not_after) : null;
  const expSoon = expiry && expiry.diff(dayjs(), "day") <= 30;
  const expired = expiry && expiry.isBefore(dayjs());

  async function run(action: "enable" | "disable") {
    setConfirm(null);
    try {
      if (action === "enable") await enable.mutateAsync();
      else await disable.mutateAsync();
    } catch {
      // the mutation's isError drives the alert below
    }
  }

  return (
    <Card withBorder padding="md" radius="md">
      <Stack>
        <Group justify="space-between">
          <Title order={4}>Log forwarding</Title>
          <Badge color={enabled ? "green" : "gray"} data-testid="lf-status">
            {enabled ? "Enabled" : "Disabled"}
          </Badge>
        </Group>

        {enabled && (
          <>
            <Text size="sm" c="dimmed">mTLS TLS syslog</Text>
            <Group gap="xs">
              <Text size="sm">Cert {s?.cert_fingerprint?.slice(0, 12)}…</Text>
              {expiry && (
                <Text size="sm" c={expired ? "red" : expSoon ? "yellow" : "dimmed"}>
                  {expired ? "expired" : "expires"} {expiry.format("YYYY-MM-DD")}
                </Text>
              )}
            </Group>
            <Group gap="xs" data-testid="lf-liveness">
              <Tooltip label="Time since the most recent log reached the lake">
                <Badge color={live.color} variant="dot">{live.label}</Badge>
              </Tooltip>
            </Group>
          </>
        )}

        {(enable.isError || disable.isError) && (
          <Alert color="red">The device rejected the change. Please retry.</Alert>
        )}

        {canWrite && (
          <Group>
            {!enabled && (
              <Button data-testid="lf-enable" loading={enable.isPending} onClick={() => setConfirm("enable")}>
                Enable
              </Button>
            )}
            {enabled && (
              <Button data-testid="lf-disable" color="red" variant="light"
                      loading={disable.isPending} onClick={() => setConfirm("disable")}>
                Disable
              </Button>
            )}
          </Group>
        )}
      </Stack>

      <ConfirmModal
        opened={confirm !== null}
        onClose={() => setConfirm(null)}
        onConfirm={() => run(confirm!)}
        title={confirm === "enable" ? "Enable log forwarding?" : "Disable log forwarding?"}
        body={confirm === "enable"
          ? "This imports a client certificate and configures a TLS syslog target on the device."
          : "This removes the syslog target and certificate from the device."}
      />
    </Card>
  );
}
```

> `dayjs().fromNow()` needs the `relativeTime` plugin. If it isn't already extended globally (check `src/main.tsx`/a dayjs setup file), either add `import relativeTime from "dayjs/plugin/relativeTime"; dayjs.extend(relativeTime);` at the top of this file, or replace `.fromNow()` with `expiry.format(...)`-style absolute text so the build stays green. Verify which is already in use and stay consistent.

- [ ] **Step 6: Mount the tab** — `frontend/src/pages/DeviceDetailPage.tsx`

Import the card: `import { LogForwardingCard } from "../components/LogForwardingCard";`. Add a tab trigger after the `firmware` tab:

```tsx
          <Tabs.Tab value="forwarding">{t.logForwarding.tab}</Tabs.Tab>
```

and a panel (after the firmware panel):

```tsx
        <Tabs.Panel value="forwarding" pt="md">
          {deviceId && <LogForwardingCard deviceId={deviceId} />}
        </Tabs.Panel>
```

Add the i18n strings to `frontend/src/i18n/en.ts` (mirror the existing `firmware`/`config` keys): a `logForwarding: { tab: "Log forwarding" }` group.

- [ ] **Step 7: Verify + build gate**

Run: `cd frontend && npm test -- logForwarding && npm run build`
Both MUST pass. Resolve any `tsc -b` issues (the openapi-fetch path union in Step 4, the dayjs plugin in Step 5).

- [ ] **Step 8: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/logs/logForwardingHooks.ts frontend/src/components/LogForwardingCard.tsx frontend/src/components/__tests__/logForwarding.test.tsx frontend/src/pages/DeviceDetailPage.tsx frontend/src/i18n/en.ts
git commit -m "feat(log-forwarding): device-page provisioning card (enable/disable + liveness)"
```

---

## Final verification

- [ ] **Backend:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npx vitest run` → all pass.
- [ ] **Security review:** dispatch `security-reviewer` (liveness keeps the mandatory tenant filter; enable/disable stay CONFIG_PUSH+CSRF+audit; no key/secret in the card — fingerprint + expiry only; OpenSearch errors degrade to "unknown", never leak). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge.

---

## Self-review notes (author)

- **Spec coverage:** cert expiry capture (Task 1: helper + column + migration + provision_device) ✓; liveness helper `latest_log_at` (Task 2) ✓; status response `cert_not_after` + `last_log_at`, OpenSearch only when enabled (Task 3) ✓; frontend card with status badge, cert fingerprint+expiry hints, 3-state liveness, enable/disable behind confirm, read_only read-only, device-page tab (Task 4) ✓; tenant-filter security on liveness ✓; testing at every layer ✓.
- **Type consistency:** `cert_not_after(cert_pem: bytes) -> datetime` (Task 1) used by `provision_device` (Task 1) and shown via the row column; `latest_log_at(settings, *, tenant_id, device_id) -> datetime | None` (Task 2) called identically by the API (Task 3) and mocked identically in the test; `LogForwardingOut` new fields (Task 3) consumed by `LogForwardingOut` TS type (Task 4).
- **Risk flags:** (a) openapi-fetch templated path union in the enable/disable hook — Step 4 notes the explicit-literal fallback; (b) `dayjs().fromNow()` relativeTime plugin — Step 5 notes extending or falling back to absolute text; (c) `ConfirmModal` confirm-button testid — Step 2 notes verifying the real testid.

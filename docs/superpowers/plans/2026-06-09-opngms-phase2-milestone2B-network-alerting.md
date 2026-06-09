# OPNGMS Phase 2 · Milestone 2B — Network Metrics + Alerting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the poller to collect network metrics (interfaces, gateways, VPN tunnels) and generate/resolve alerts on state changes (device down, gateway down).

**Architecture:** The connector gains `get_interfaces`/`get_gateways`/`get_vpn_status` (one method per group, normalised, **endpoints to verify**, mocked with respx). `collect_and_store` now also collects network metrics (written to the hypertable with `label` = interface/gateway/tunnel name) and returns a state (`PollState`) consumed by a new `evaluate_alerts(session, device, state)`, which **reconciles** alerts: opens an alert if the down condition is true and no open alert exists, resolves it when the condition clears. Relational `alerts` table (not a hypertable). The poller writes as owner; RLS on `alerts` (as for `metrics`) is deferred to 2C with the read path.

**Tech Stack:** same as 2A (FastAPI/SQLAlchemy async, TimescaleDB, ARQ, httpx, respx, pytest).

---

## Spec reference
Implements §4.2 (alerts), §6 (network connector), §9-2B of the spec
`docs/superpowers/specs/2026-06-09-opngms-phase2-monitoring-design.md`.

## Sequencing decisions
- **RLS on `alerts`: deferred to 2C** (with read API), consistent with the `metrics` choice in 2A. In
  2B `alerts` is a plain table; the poller writes as owner.
- **Idempotent alerting via reconciliation:** no previous state needed — `evaluate_alerts`
  compares the current condition with *open* alerts and opens/resolves accordingly.

## File structure
```
backend/app/
  connectors/opnsense/client.py   # MODIFY: get_interfaces/get_gateways/get_vpn_status
  services/monitoring.py          # MODIFY: collect_and_store collects network + returns PollState
  services/alerting.py            # NEW: evaluate_alerts (device.down/gateway.down reconciliation)
  models/alert.py                 # NEW
  models/__init__.py              # MODIFY: export Alert
  worker.py                       # MODIFY: poll_device calls evaluate_alerts
  migrations/versions/0006_alerts.py  # NEW
backend/tests/
  test_connector_network.py
  test_monitoring_network.py      # (+ update existing FakeClients)
  test_alert_model.py
  test_alerting.py
  test_2b_integration.py
```

---

## Task 1: Connector — get_interfaces / get_gateways / get_vpn_status

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Create `backend/tests/test_connector_network.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_connector_network.py`:
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://203.0.113.10"


@respx.mock
async def test_get_interfaces():
    respx.get(f"{BASE}/api/diagnostics/interface/getInterfaceStatistics").mock(
        return_value=httpx.Response(200, json={
            "interfaces": [
                {"name": "igb0", "status": "up", "bytes_received": 1000, "bytes_transmitted": 2000},
            ]
        })
    )
    ifs = await OpnsenseClient(BASE, "k", "s").get_interfaces()
    assert ifs == [{"name": "igb0", "up": True, "bytes_in": 1000.0, "bytes_out": 2000.0}]


@respx.mock
async def test_get_gateways():
    respx.get(f"{BASE}/api/routes/gateway/status").mock(
        return_value=httpx.Response(200, json={
            "items": [
                {"name": "WAN_GW", "status": "none", "delay": "12.3 ms", "loss": "0.0 %"},
                {"name": "WAN2_GW", "status": "down", "delay": "", "loss": "100.0 %"},
            ]
        })
    )
    gws = await OpnsenseClient(BASE, "k", "s").get_gateways()
    by = {g["name"]: g for g in gws}
    assert by["WAN_GW"]["up"] is True and by["WAN_GW"]["rtt_ms"] == 12.3
    assert by["WAN2_GW"]["up"] is False and by["WAN2_GW"]["loss_pct"] == 100.0


@respx.mock
async def test_get_vpn_status():
    respx.get(f"{BASE}/api/wireguard/service/show").mock(
        return_value=httpx.Response(200, json={"tunnels": [{"name": "wg0", "connected": True}]})
    )
    vpn = await OpnsenseClient(BASE, "k", "s").get_vpn_status()
    assert vpn == [{"name": "wg0", "up": True}]
```
Run: `cd backend && .venv/bin/python -m pytest tests/test_connector_network.py -v` → FAIL.

- [ ] **Step 2: Implement** — add to `OpnsenseClient` (helpers for the noisy parsing; all go through `self._get`, so SSRF guard + error normalization apply). NOTE: endpoint paths + field names **TO BE VERIFIED** against a real OPNsense device:
```python
    @staticmethod
    def _num(v) -> float:
        """Extracts the first float from a string like '12.3 ms' / '0.0 %' / a number."""
        import re

        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
        return float(m.group()) if m else 0.0

    async def get_interfaces(self) -> list[dict]:
        data = await self._get("diagnostics/interface/getInterfaceStatistics")
        out = []
        for it in data.get("interfaces", []):
            out.append({
                "name": it.get("name", ""),
                "up": it.get("status") == "up",
                "bytes_in": self._num(it.get("bytes_received")),
                "bytes_out": self._num(it.get("bytes_transmitted")),
            })
        return out

    async def get_gateways(self) -> list[dict]:
        data = await self._get("routes/gateway/status")
        out = []
        for g in data.get("items", []):
            status = str(g.get("status", "")).lower()
            out.append({
                "name": g.get("name", ""),
                "up": status not in ("down", "force_down"),  # 'none'/''/'delay' = up; 'down' = down
                "rtt_ms": self._num(g.get("delay")),
                "loss_pct": self._num(g.get("loss")),
            })
        return out

    async def get_vpn_status(self) -> list[dict]:
        data = await self._get("wireguard/service/show")
        return [
            {"name": t.get("name", ""), "up": bool(t.get("connected"))}
            for t in data.get("tunnels", [])
        ]
```

- [ ] **Step 3: Run + commit**
```bash
cd backend && .venv/bin/python -m pytest tests/test_connector_network.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_network.py
git commit -m "feat(backend): connector get_interfaces/get_gateways/get_vpn_status"
```
Expected: 3 new pass; full suite green (101 passed: 98 + 3).

---

## Task 2: collect_and_store collects network metrics + returns PollState

**Files:** Modify `backend/app/services/monitoring.py`, `backend/tests/test_monitoring.py`, `backend/tests/test_poller_e2e.py`; Create `backend/tests/test_monitoring_network.py`

- [ ] **Step 1: Update existing FakeClients** — the `FakeClient` in `tests/test_monitoring.py` and `tests/test_poller_e2e.py` now need the 3 network methods as well (otherwise `collect_and_store` fails with AttributeError). Add to EVERY FakeClient (read the files, add the methods):
```python
    async def get_interfaces(self):
        return [{"name": "igb0", "up": True, "bytes_in": 100.0, "bytes_out": 200.0}]

    async def get_gateways(self):
        return [{"name": "WAN_GW", "up": True, "rtt_ms": 5.0, "loss_pct": 0.0}]

    async def get_vpn_status(self):
        return [{"name": "wg0", "up": True}]
```
(The `FailClient` in test_poller_e2e raises in get_system_info, so it never reaches the network — but as a safety measure add the methods there too, returning `[]`.)

- [ ] **Step 2: Failing test** — `backend/tests/test_monitoring_network.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.metric import Metric
from app.services.monitoring import collect_and_store


class NetClient:
    async def get_system_info(self):
        return {"cpu_pct": 1.0, "mem_pct": 2.0, "disk_pct": 3.0, "uptime_seconds": 4}

    async def get_firmware_status(self):
        return {"product_version": "24.7"}

    async def get_interfaces(self):
        return [{"name": "igb0", "up": True, "bytes_in": 100.0, "bytes_out": 200.0}]

    async def get_gateways(self):
        return [{"name": "WAN_GW", "up": False, "rtt_ms": 0.0, "loss_pct": 100.0}]

    async def get_vpn_status(self):
        return [{"name": "wg0", "up": True}]


async def _device(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def test_network_metrics_written_with_labels(db_engine):
    _, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        device = await s.get(Device, did)
        state = await collect_and_store(s, device, NetClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with f() as s:
        rows = (await s.execute(select(Metric).where(Metric.device_id == did))).scalars().all()
        labeled = {(r.metric, r.label): r.value for r in rows}
        assert labeled[("iface.bytes_in", "igb0")] == 100.0
        assert labeled[("gateway.up", "WAN_GW")] == 0.0
        assert labeled[("vpn.up", "wg0")] == 1.0
    # collect_and_store now returns a state with gateways (for alerting)
    assert state.reachable is True
    assert any(g["name"] == "WAN_GW" and g["up"] is False for g in state.gateways)
```
Run → FAIL.

- [ ] **Step 3: Implement** — in `backend/app/services/monitoring.py`, add a `PollState` and extend `collect_and_store` to collect network data and return state:
```python
from dataclasses import dataclass, field


@dataclass
class PollState:
    reachable: bool
    gateways: list[dict] = field(default_factory=list)


async def collect_and_store(session, device, client, now) -> PollState:
    try:
        info = await client.get_system_info()
        fw = await client.get_firmware_status()
        interfaces = await client.get_interfaces()
        gateways = await client.get_gateways()
        vpn = await client.get_vpn_status()
    except OpnsenseError:
        device.status = "unverified"
        return PollState(reachable=False)
    rows = [
        _metric(now, device, "cpu.pct", info["cpu_pct"]),
        _metric(now, device, "mem.pct", info["mem_pct"]),
        _metric(now, device, "disk.pct", info["disk_pct"]),
        _metric(now, device, "uptime.seconds", info["uptime_seconds"]),
    ]
    for it in interfaces:
        rows.append(_metric(now, device, "iface.bytes_in", it["bytes_in"], it["name"]))
        rows.append(_metric(now, device, "iface.bytes_out", it["bytes_out"], it["name"]))
        rows.append(_metric(now, device, "iface.up", 1.0 if it["up"] else 0.0, it["name"]))
    for g in gateways:
        rows.append(_metric(now, device, "gateway.rtt_ms", g["rtt_ms"], g["name"]))
        rows.append(_metric(now, device, "gateway.loss_pct", g["loss_pct"], g["name"]))
        rows.append(_metric(now, device, "gateway.up", 1.0 if g["up"] else 0.0, g["name"]))
    for v in vpn:
        rows.append(_metric(now, device, "vpn.up", 1.0 if v["up"] else 0.0, v["name"]))
    session.add_all(rows)
    device.status = "reachable"
    device.last_seen = now
    version = fw.get("product_version")
    if version:
        device.firmware_version = version
    await session.flush()
    return PollState(reachable=True, gateways=gateways)
```
NOTE: `collect_and_store` now RETURNS `PollState` (previously `None`). Existing tests that ignore the return value continue to pass; the poller (Task 5) will use the return.

- [ ] **Step 4: Run + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_monitoring_network.py tests/test_monitoring.py tests/test_poller_e2e.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/services/monitoring.py backend/tests/test_monitoring.py backend/tests/test_poller_e2e.py backend/tests/test_monitoring_network.py
git commit -m "feat(backend): collect_and_store collects network metrics + returns PollState"
```
Expected: new + updated tests pass; full suite green.

---

## Task 3: Alert model + migration 0006

**Files:** Create `backend/app/models/alert.py`, `backend/migrations/versions/0006_alerts.py`; Modify `backend/app/models/__init__.py`; Create `backend/tests/test_alert_model.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_alert_model.py`:
```python
from app.models import Base
from app.models.alert import Alert


def test_alert_table_registered():
    assert "alerts" in Base.metadata.tables
    cols = {c.name for c in Alert.__table__.columns}
    assert {"id", "tenant_id", "device_id", "type", "label", "severity", "opened_at", "resolved_at", "details"} <= cols
```

- [ ] **Step 2: Model** — `backend/app/models/alert.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Alert(UUIDPKMixin, Base):
    __tablename__ = "alerts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String)
    label: Mapped[str] = mapped_column(String, default="")
    severity: Mapped[str] = mapped_column(String, default="warning")
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
```
Add `Alert` to `backend/app/models/__init__.py`.

- [ ] **Step 3: Migration** — `backend/migrations/versions/0006_alerts.py`:
```python
"""alerts table + partial unique index on active alert"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(), nullable=False, server_default="warning"),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_alerts_tenant_id", "alerts", ["tenant_id"])
    op.create_index("ix_alerts_device_id", "alerts", ["device_id"])
    # One active alert per (device, type, label):
    op.create_index(
        "uq_alerts_active",
        "alerts",
        ["device_id", "type", "label"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("alerts")
```
NOTE: the Python `default=dict` on the model + `server_default '{}'::jsonb` on the migration (as for audit_log). The partial unique index must also be declared on the MODEL to keep `alembic check` clean — add to `Alert.__table_args__`:
```python
from sqlalchemy import Index, text
    __table_args__ = (
        Index("uq_alerts_active", "device_id", "type", "label", unique=True,
              postgresql_where=text("resolved_at IS NULL")),
    )
```
(the `ix_alerts_tenant_id`/`ix_alerts_device_id` indexes come from `index=True` on the fields.)

- [ ] **Step 4: Apply + verify alembic check + tests**
```bash
cd backend
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic upgrade head
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic check
.venv/bin/python -m pytest tests/test_alert_model.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
```
Expected: upgrade ok; **alembic check clean** (partial unique index declared on model); model test passes; full suite green. Verify downgrade/upgrade on test DB.

- [ ] **Step 5: commit**
```bash
git add backend/app/models/alert.py backend/app/models/__init__.py backend/migrations/versions/0006_alerts.py backend/tests/test_alert_model.py
git commit -m "feat(backend): alerts table (model + migration 0006, unique active per device/type/label)"
```

---

## Task 4: Alert engine — evaluate_alerts (device.down + gateway.down)

**Files:** Create `backend/app/services/alerting.py`, `backend/tests/test_alerting.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_alerting.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.alert import Alert
from app.models.device import Device
from app.services.alerting import evaluate_alerts
from app.services.monitoring import PollState


async def _device(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _active(s, did):
    return (await s.execute(select(Alert).where(Alert.device_id == did, Alert.resolved_at.is_(None)))).scalars().all()


async def test_device_down_opens_then_resolves(db_engine):
    tid, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    # 1) device unreachable -> opens device.down
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=False))
        await s.commit()
    async with f() as s:
        active = await _active(s, did)
        assert [a.type for a in active] == ["device.down"]
    # 2) unreachable again -> does NOT duplicate
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=False))
        await s.commit()
    async with f() as s:
        assert len(await _active(s, did)) == 1
    # 3) comes back up -> resolves
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True))
        await s.commit()
    async with f() as s:
        assert await _active(s, did) == []


async def test_gateway_down_opens_and_resolves(db_engine):
    tid, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True, gateways=[{"name": "WAN_GW", "up": False}]))
        await s.commit()
    async with f() as s:
        active = await _active(s, did)
        assert [(a.type, a.label) for a in active] == [("gateway.down", "WAN_GW")]
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True, gateways=[{"name": "WAN_GW", "up": True}]))
        await s.commit()
    async with f() as s:
        assert await _active(s, did) == []
```

- [ ] **Step 2: Implement** — `backend/app/services/alerting.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.services.monitoring import PollState


async def _open_alerts(session: AsyncSession, device: Device) -> dict[tuple[str, str], Alert]:
    result = await session.execute(
        select(Alert).where(Alert.device_id == device.id, Alert.resolved_at.is_(None))
    )
    return {(a.type, a.label): a for a in result.scalars().all()}


def _open(device: Device, type_: str, label: str = "") -> Alert:
    return Alert(tenant_id=device.tenant_id, device_id=device.id, type=type_, label=label)


async def evaluate_alerts(session: AsyncSession, device: Device, state: PollState) -> None:
    """Reconciles alerts with current state: opens new downs, resolves cleared ones.

    Idempotent: uses ONLY current state + open alerts (partial unique constraint
    also prevents duplicates on (device, type, label)).
    """
    now = datetime.now(timezone.utc)
    open_alerts = await _open_alerts(session, device)

    # device.down (label '')
    key = ("device.down", "")
    if not state.reachable and key not in open_alerts:
        session.add(_open(device, "device.down"))
    elif state.reachable and key in open_alerts:
        open_alerts[key].resolved_at = now

    # gateway.down (label = gateway name), only evaluated if device is reachable
    if state.reachable:
        down_now = {g["name"] for g in state.gateways if not g["up"]}
        for name in down_now:
            if ("gateway.down", name) not in open_alerts:
                session.add(_open(device, "gateway.down", name))
        # resolve gateway.down alerts whose gateways are back up / no longer down
        for (type_, label), alert in open_alerts.items():
            if type_ == "gateway.down" and label not in down_now:
                alert.resolved_at = now

    await session.flush()
```

- [ ] **Step 3: Run + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_alerting.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/services/alerting.py backend/tests/test_alerting.py
git commit -m "feat(backend): evaluate_alerts (device.down + gateway.down, idempotent reconciliation)"
```
Expected: alerting tests pass; full suite green.

---

## Task 5: Wire poll_device → evaluate_alerts + integration

**Files:** Modify `backend/app/worker.py`; Create `backend/tests/test_2b_integration.py`

- [ ] **Step 1: Wire** — in `backend/app/worker.py`, `poll_device` now captures the `PollState` returned by `collect_and_store` and calls `evaluate_alerts` BEFORE the commit:
```python
        state = await collect_and_store(session, device, client, now=datetime.now(timezone.utc))
        from app.services.alerting import evaluate_alerts
        await evaluate_alerts(session, device, state)
        await session.commit()
        return device.status
```
(importing `evaluate_alerts` at module level at the top is preferable — adapt accordingly.)

- [ ] **Step 2: Integration test** — `backend/tests/test_2b_integration.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.alert import Alert
from app.services.alerting import evaluate_alerts
from app.services.monitoring import collect_and_store


class DownGwClient:
    async def get_system_info(self): return {"cpu_pct": 1.0, "mem_pct": 2.0, "disk_pct": 3.0, "uptime_seconds": 4}
    async def get_firmware_status(self): return {"product_version": "24.7"}
    async def get_interfaces(self): return []
    async def get_gateways(self): return [{"name": "WAN_GW", "up": False, "rtt_ms": 0.0, "loss_pct": 100.0}]
    async def get_vpn_status(self): return []


async def test_poll_collects_and_opens_gateway_alert(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    async with f() as s:
        from app.models.device import Device
        device = await s.get(Device, did)
        state = await collect_and_store(s, device, DownGwClient(), now=datetime.now(timezone.utc))
        await evaluate_alerts(s, device, state)
        await s.commit()
    async with f() as s:
        active = (await s.execute(select(Alert).where(Alert.device_id == did, Alert.resolved_at.is_(None)))).scalars().all()
        assert [(a.type, a.label) for a in active] == [("gateway.down", "WAN_GW")]
        assert all(a.tenant_id == tid for a in active)
```

- [ ] **Step 3: Run whole suite + commit**
```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_2b_integration.py tests/test_worker_config.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/worker.py backend/tests/test_2b_integration.py
git commit -m "feat(backend): poll_device evaluates alerts after collection (2B integration)"
```
Expected: integration test passes; full suite green.

---

## Self-review (spec → task mapping)
- **Spec §6 (network connector)** → Task 1.
- **Spec §4.1 (network metrics with label)** → Task 2.
- **Spec §4.2 (alerts) + §9-2B (alert engine)** → Task 3 (model/migration), Task 4 (evaluate_alerts),
  Task 5 (poller wiring).
- **Deferred (by design):** RLS on `alerts` + alerts API (2C); configurable thresholds, notifications
  (post-MVP).

**Scope notes / debt:**
- RLS on `alerts` → 2C (with read API); in 2B the poller writes as owner.
- OPNsense network endpoints (`diagnostics/interface/getInterfaceStatistics`, `routes/gateway/status`,
  `wireguard/service/show`) + field names **TO BE VERIFIED** against a real device; mocked with respx.
- Only WireGuard in `get_vpn_status` for MVP (OpenVPN/IPsec can be added as further calls).
- `evaluate_alerts` reconciles by current state; a gateway that DISAPPEARS from the list (no longer
  reported) → its alert stays open (rare edge case; can be extended by resolving absent gateways).

**Placeholder scan:** every step has concrete code/commands. Uncertainties (OPNsense endpoints) are
explicit and isolated behind contracts pinned by tests.
**Type consistency:** `collect_and_store(...) -> PollState(reachable, gateways)`, `evaluate_alerts(
session, device, state)`, `Alert(tenant_id, device_id, type, label, severity, opened_at,
resolved_at, details)`, metrics `iface.*`/`gateway.*`/`vpn.up` with `label`, consistent across Tasks 1-5.

---

## Technical debt (from final holistic review — READY TO MERGE)

Zero Critical/Important issues. Correct and idempotent alert engine (app guard + partial index agree),
structural multi-tenancy (metric/alert inherit `tenant_id` from device), correct resilience,
connector as single defensive boundary, clean `alembic check`. To track:

1. **RLS on `alerts` and `metrics` (2C):** both outside `TENANT_TABLES` (only `devices`); the
   worker writes as owner (intentional), but RLS must land in 2C BEFORE any user-facing read path.
2. **Read API + cross-tenant isolation test (2C):** `GET .../metrics`, `.../health`,
   `.../alerts?active=` with negatives proving isolation under RLS.
3. **OPNsense endpoints TO BE VERIFIED** against a real device (interfaces/gateways/VPN + string
   formats for `_num` + `product_version`).
4. **Alert notification channels** (email/webhook on open/resolve) — absent.
5. **Configurable thresholds + threshold-based metric alerts** (CPU/mem/disk/loss/RTT) — today only
   device.down/gateway.down are hard-coded.
6. **OpenVPN/IPsec** in `get_vpn_status` (today WireGuard only).
7. **Empty entity names:** skip/deduplicate interfaces/gateways with `name=""` to avoid PK/unique
   collisions.
8. **Per-device job dedup** (`_job_id` in `enqueue_device_polls`) and/or graceful handling
   of `IntegrityError` on alert insert, to make overlapping polls a clean no-op.
9. **`device.status = "unreachable"`** mentioned in comments but never written — decide whether to
   distinguish it from `unverified`.

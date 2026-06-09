# OPNGMS — Phase 2 / Milestone 2C: Metrics / Health / Alerts API (tenant-scoped + RLS) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose via REST API the time-series metrics, fleet health summary, and alerts that the poller (2A/2B) already writes, isolated per customer by Postgres RLS.

**Architecture:** Three read-only endpoints under `/api/tenants/{tenant_id}/...`, gated by `require_tenant(DEVICE_VIEW)` + tenant-context (which sets `app.current_tenant`). Postgres RLS filters `metrics` and `alerts` by tenant exactly as for `devices` (double layer: application-layer filter in the repository + DB policy). A new migration extends RLS to both tables and grants privileges to `opngms_app`, including an explicit grant on the `metrics` hypertable to propagate it to TimescaleDB chunks. Downsampling of long series is done on-the-fly with `time_bucket()` (the materialised continuous aggregate is deferred).

**Tech Stack:** FastAPI async, SQLAlchemy 2.0 async + asyncpg, TimescaleDB (hypertable `metrics`), Pydantic v2, pytest + pytest-asyncio.

---

## Context for the implementer (read before starting)

You are in an existing codebase with established patterns. **Follow them exactly.** Key references:

- **Tenant-scoped router**: `app/api/devices.py` — `APIRouter(prefix="/api/tenants/{tenant_id}/...")`, each endpoint depends on `ctx = Depends(require_tenant(Action.DEVICE_VIEW))` and `session = Depends(get_session)`. `require_tenant` (in `app/core/deps.py`) calls `tenant_context`, which **sets `app.current_tenant`** on the session (`set_tenant_context`) → RLS activates. You do not need to manage RLS in the endpoint: it comes for free from the dependency.
- **Tenant-scoped repository**: `app/repositories/device.py` — constructed with `(session, tenant_id)`, every query filters `WHERE tenant_id == self.tenant_id` (application filter) **in addition** to DB RLS. Replicate this pattern for metrics and alerts.
- **RLS — single source of truth**: `app/core/rls.py`. `TENANT_TABLES` lists tables with RLS (today only `["devices"]`). `policy_create_statement(table)` generates the `CREATE POLICY tenant_isolation`. The test conftest (`tests/conftest.py`, fixture `db_engine`) calls `enable_rls_statements()` on all `TENANT_TABLES`: **as soon as you add `metrics`/`alerts` there, tests will protect them automatically.**
- **DB roles**: `app/core/db_roles.py`. Migrations/the poller run as superuser owner `opngms` (bypasses RLS — trusted infrastructure). The API connects as `opngms_app` (NOSUPERUSER NOBYPASSRLS) → RLS applies. `grant_app_role_statements()` grants SELECT/INSERT/UPDATE/DELETE `ON ALL TABLES IN SCHEMA public` + default privileges.
- **Models**: `app/models/metric.py` (`Metric`: composite PK `time,device_id,metric,label`, + `tenant_id`, `value`), `app/models/alert.py` (`Alert`: `id` PK, `tenant_id`, `device_id`, `type`, `label`, `severity`, `opened_at`, `resolved_at` nullable, `details` JSONB).
- **Pydantic schemas**: `app/schemas/device.py` — `DeviceOut` uses `model_config = {"from_attributes": True}`. Replicate the style.
- **Router registration**: `app/main.py` — `app.include_router(...)`.
- **RBAC**: `app/core/rbac.py` — `Action.DEVICE_VIEW` is granted to `tenant_admin/operator/read_only` (correct for read-only endpoints). Reuse it, do **not** create new Actions.
- **Isolation tests**: `tests/test_rls_isolation.py` (raw SELECT with `SET ROLE opngms_app`) and `tests/test_devices_rls_api.py` (via `app_role_api_client`, real connection as `opngms_app`). Relevant fixtures in `tests/conftest.py`: `db_engine`, `two_tenants`, `api_client` (owner), `app_role_api_client` (real opngms_app). `tests/factories.py` has `make_tenant`.

**Test command** (single DB, owner+app role in the same one): from `backend/` directory
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
The test DB runs in Docker (`docker compose ps` → `db` service). The suite currently has **108 green tests**.

**Deliberate deviation from spec (YAGNI):** spec §7 prescribes reading from a *continuous aggregate* `metrics_5m` for long ranges. For the MVP we **defer it**: downsampling is done on-the-fly with `time_bucket()` (same response shape, correct for 100-300 devices / 30 days of retention). The materialised CAGG + its retention remain technical debt for future optimisation (Task 6 records it).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/core/rls.py` | Add `metrics`, `alerts` to `TENANT_TABLES` | Modify |
| `migrations/versions/0007_rls_metrics_alerts.py` | Enable RLS+policy on metrics/alerts + grant to opngms_app | Create |
| `app/schemas/metric.py` | `MetricPoint`, `MetricSeriesOut` | Create |
| `app/schemas/alert.py` | `AlertOut` | Create |
| `app/schemas/health.py` | `HealthOut` | Create |
| `app/repositories/metric.py` | `MetricRepository` (series with time_bucket, last value) | Create |
| `app/repositories/alert.py` | `AlertRepository` (list active/historical) | Create |
| `app/api/monitoring.py` | Router: metrics series, health, alerts | Create |
| `app/main.py` | `include_router(monitoring_router)` | Modify |
| `tests/test_rls_isolation.py` | Extend: metrics/alerts in TENANT_TABLES + raw isolation | Modify |
| `tests/test_metric_repository.py` | Series + last value, tenant-scoped | Create |
| `tests/test_alert_repository.py` | List active/all alerts, tenant-scoped | Create |
| `tests/test_monitoring_api.py` | Endpoint happy-path + RBAC (owner client) | Create |
| `tests/test_monitoring_rls_api.py` | Cross-tenant isolation via real opngms_app | Create |

---

## Task 1: Extend RLS to `metrics` and `alerts`

**Files:**
- Modify: `app/core/rls.py:7`
- Create: `migrations/versions/0007_rls_metrics_alerts.py`
- Modify: `tests/test_rls_isolation.py`

This is the security foundation: without RLS on both tables, any endpoint could leak metrics/alerts cross-tenant. We proceed TDD starting from the static contract, then the migration, then real isolation.

- [ ] **Step 1: Write the failing test (static contract)**

In `tests/test_rls_isolation.py`, modify `test_rls_statements_cover_devices` by adding immediately after a new function:

```python
def test_rls_statements_cover_metrics_and_alerts():
    assert "metrics" in TENANT_TABLES
    assert "alerts" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    for table in ("metrics", "alerts"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py::test_rls_statements_cover_metrics_and_alerts -v`
Expected: FAIL with `assert 'metrics' in ['devices']`.

- [ ] **Step 3: Add the tables to `TENANT_TABLES`**

In `app/core/rls.py`, line 7:

```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts"]
```

- [ ] **Step 4: Run the test and verify it passes**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py::test_rls_statements_cover_metrics_and_alerts -v`
Expected: PASS.

- [ ] **Step 5: Write migration 0007**

Create `migrations/versions/0007_rls_metrics_alerts.py`. Enable RLS+policy ONLY on the two new tables (`devices` is already covered by 0002/0003) and re-grant privileges to `opngms_app` (now that the tables exist), with an explicit grant on `metrics` so TimescaleDB propagates the privilege to chunks.

```python
"""RLS on metrics + alerts; grant to opngms_app (with Timescale chunk propagation)"""

from alembic import op

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_NEW_TABLES = ["metrics", "alerts"]


def upgrade() -> None:
    for table in _NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(policy_create_statement(table))
    # The metrics/alerts tables were created AFTER the GRANT ON ALL TABLES in migration 0003:
    # re-run the grants now that they exist. On `metrics` (hypertable) the explicit GRANT
    # propagates the privilege to TimescaleDB chunks (existing and future).
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    op.execute(f"GRANT SELECT ON metrics TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"REVOKE SELECT ON metrics FROM {APP_ROLE}")
    for table in _NEW_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 6: Add raw isolation test for metrics and alerts**

In `tests/test_rls_isolation.py`, add at the end. Inserts a metric and an alert for each tenant (as owner, which bypasses RLS), then reads as real `opngms_app` verifying isolation. Reuse the pattern from `test_app_role_connection_enforces_rls`.

```python
async def test_metrics_alerts_isolated_cross_tenant(db_engine, two_tenants):
    """metrics and alerts: real opngms_app connection sees only the tenant in context.

    Also proves RLS propagation to Timescale hypertable chunks.
    """
    import os
    import uuid as _uuid
    from datetime import datetime, timezone

    tenant_a, tenant_b = two_tenants
    # device_id can be anything: RLS filters on tenant_id, not a real device for the metric.
    owner_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner_factory() as s:  # owner = superuser -> bypasses RLS, inserts for both
        for tid, val in ((tenant_a, 1.0), (tenant_b, 2.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": _uuid.uuid4(), "tid": tid, "v": val},
            )
        # alert: device_id must reference an existing device (FK). two_tenants has fw-a/fw-b.
        for tid, name in ((tenant_a, "fw-a"), (tenant_b, "fw-b")):
            dev_id = (
                await s.execute(text("SELECT id FROM devices WHERE name = :n"), {"n": name})
            ).scalar_one()
            await s.execute(
                text(
                    "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity) "
                    "VALUES (:id, :tid, :did, 'device.down', '', 'critical')"
                ),
                {"id": _uuid.uuid4(), "tid": tid, "did": dev_id},
            )
        await s.commit()

    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            vals = (await s.execute(text("SELECT value FROM metrics"))).scalars().all()
            assert vals == [1.0]
            sev = (await s.execute(text("SELECT severity FROM alerts"))).scalars().all()
            assert sev == ["critical"]
        async with factory() as s2:
            # no context -> fail-closed on both
            assert (await s2.execute(text("SELECT value FROM metrics"))).scalars().all() == []
            assert (await s2.execute(text("SELECT id FROM alerts"))).scalars().all() == []
    finally:
        await engine.dispose()
```

- [ ] **Step 7: Run the full RLS suite**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py -v`
Expected: all PASS (including the new metrics/alerts isolation). If `test_metrics_alerts_isolated_cross_tenant` shows that `opngms_app` sees 0 rows **even with context** on `metrics`, it is the grant→chunk propagation problem: verify that Step 5 executed `GRANT SELECT ON metrics`. The conftest already grants via `grant_app_role_statements()` before inserts, so new chunks inherit.

- [ ] **Step 8: Verify `alembic check` on a clean DB**

```bash
docker compose exec -T db psql -U opngms -d postgres -c "DROP DATABASE IF EXISTS opngms_check;"
docker compose exec -T db psql -U opngms -d postgres -c "CREATE DATABASE opngms_check;"
docker compose exec -T db psql -U opngms -d opngms_check -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_check" \
DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_check" \
SESSION_SECRET="x" MASTER_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
.venv/bin/alembic upgrade head && \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_check" \
DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_check" \
SESSION_SECRET="x" MASTER_KEY="$(.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
.venv/bin/alembic check
docker compose exec -T db psql -U opngms -d postgres -c "DROP DATABASE IF EXISTS opngms_check;"
```
Expected: `upgrade head` reaches 0007; `alembic check` → "No new upgrade operations detected." (policies/grants are not model objects, so no drift).

- [ ] **Step 9: Commit**

```bash
git add app/core/rls.py migrations/versions/0007_rls_metrics_alerts.py tests/test_rls_isolation.py
git commit -m "feat(backend): RLS on metrics+alerts (migration 0007 + cross-tenant isolation)"
```

---

## Task 2: Repository + schema + metrics series endpoint

**Files:**
- Create: `app/schemas/metric.py`
- Create: `app/repositories/metric.py`
- Create: `app/api/monitoring.py`
- Modify: `app/main.py`
- Create: `tests/test_metric_repository.py`

- [ ] **Step 1: Write the failing repository test**

Create `tests/test_metric_repository.py`. Inserts some metrics for the active tenant (as owner) and verifies that the repository, under `SET ROLE opngms_app` + context, returns correct series and last value.

```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.metric import MetricRepository


async def _seed(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypasses RLS
        for i, v in enumerate((10.0, 20.0, 30.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "tid": tenant_id, "v": v},
            )
        await s.commit()
    return base


async def test_series_returns_points_in_order(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load", base - timedelta(minutes=1), base + timedelta(minutes=10), None
        )
    assert [p.value for p in points] == [10.0, 20.0, 30.0]
    assert all(p.label == "" for p in points)


async def test_last_returns_latest_per_label(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        last = await repo.last(device_id, "cpu.load")
    assert [p.value for p in last] == [30.0]


async def test_series_bucket_downsamples(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    base = await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        repo = MetricRepository(s, tenant_a)
        points = await repo.series(
            device_id, "cpu.load",
            base - timedelta(minutes=1), base + timedelta(minutes=10),
            timedelta(hours=1),  # one bucket -> average (10+20+30)/3 = 20
        )
    assert len(points) == 1
    assert points[0].value == 20.0
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `... .venv/bin/python -m pytest tests/test_metric_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: app.repositories.metric` / `app.schemas.metric`.

- [ ] **Step 3: Write the metrics schema**

Create `app/schemas/metric.py`:

```python
from datetime import datetime

from pydantic import BaseModel


class MetricPoint(BaseModel):
    time: datetime
    label: str
    value: float


class MetricSeriesOut(BaseModel):
    metric: str
    points: list[MetricPoint]
    last: list[MetricPoint]  # last value per label
```

- [ ] **Step 4: Write the metrics repository**

Create `app/repositories/metric.py`. Application filter `tenant_id` + `device_id` (double isolation with RLS). `series` with optional `time_bucket`; `last` = last point per label via `DISTINCT ON`.

```python
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.metric import MetricPoint


class MetricRepository:
    """Time-series reads for a tenant. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def series(
        self,
        device_id: uuid.UUID,
        metric: str,
        frm: datetime,
        to: datetime,
        bucket: timedelta | None,
    ) -> list[MetricPoint]:
        params = {
            "tid": self.tenant_id,
            "did": device_id,
            "metric": metric,
            "frm": frm,
            "to": to,
        }
        if bucket is not None:
            params["bucket"] = bucket
            sql = text(
                "SELECT time_bucket(:bucket, time) AS t, label, avg(value) AS v "
                "FROM metrics "
                "WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
                "  AND time >= :frm AND time < :to "
                "GROUP BY t, label ORDER BY t, label"
            )
        else:
            sql = text(
                "SELECT time AS t, label, value AS v "
                "FROM metrics "
                "WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
                "  AND time >= :frm AND time < :to "
                "ORDER BY time, label"
            )
        rows = (await self.session.execute(sql, params)).all()
        return [MetricPoint(time=r.t, label=r.label, value=float(r.v)) for r in rows]

    async def last(self, device_id: uuid.UUID, metric: str) -> list[MetricPoint]:
        sql = text(
            "SELECT DISTINCT ON (label) time AS t, label, value AS v "
            "FROM metrics "
            "WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
            "ORDER BY label, time DESC"
        )
        rows = (
            await self.session.execute(
                sql, {"tid": self.tenant_id, "did": device_id, "metric": metric}
            )
        ).all()
        return [MetricPoint(time=r.t, label=r.label, value=float(r.v)) for r in rows]
```

- [ ] **Step 5: Run the repository tests and verify they pass**

Run: `... .venv/bin/python -m pytest tests/test_metric_repository.py -v`
Expected: PASS (3/3).

- [ ] **Step 6: Write the router with the series endpoint + register it**

Create `app/api/monitoring.py`. Parse query params `metric` (required), `from`/`to` (default: last 24h), `bucket` (ISO-8601 seconds optional → `timedelta`). Returns `MetricSeriesOut`.

```python
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.repositories.metric import MetricRepository
from app.schemas.metric import MetricSeriesOut

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["monitoring"])


@router.get("/devices/{device_id}/metrics", response_model=MetricSeriesOut)
async def get_device_metrics(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    metric: str = Query(..., description="Metric name, e.g. 'cpu.load'"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    bucket_seconds: int | None = Query(None, alias="bucket", ge=1),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> MetricSeriesOut:
    now = datetime.now(timezone.utc)
    frm = from_ or (now - timedelta(hours=24))
    end = to or now
    bucket = timedelta(seconds=bucket_seconds) if bucket_seconds else None
    repo = MetricRepository(session, tenant_id)
    points = await repo.series(device_id, metric, frm, end, bucket)
    last = await repo.last(device_id, metric)
    return MetricSeriesOut(metric=metric, points=points, last=last)
```

In `app/main.py`, add the import and registration alongside the other routers:

```python
from app.api.monitoring import router as monitoring_router
```
and after `app.include_router(me_tenants_router)`:
```python
app.include_router(monitoring_router)
```

- [ ] **Step 7: Run the full suite**

Run: `... .venv/bin/python -m pytest -q`
Expected: all PASS (108 + new repository tests). The endpoint will be tested in Task 5.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/metric.py app/repositories/metric.py app/api/monitoring.py app/main.py tests/test_metric_repository.py
git commit -m "feat(backend): metrics series endpoint (time_bucket repository + last value)"
```

---

## Task 3: Repository + schema + alerts endpoint

**Files:**
- Create: `app/schemas/alert.py`
- Create: `app/repositories/alert.py`
- Modify: `app/api/monitoring.py`
- Create: `tests/test_alert_repository.py`

- [ ] **Step 1: Write the failing repository test**

Create `tests/test_alert_repository.py`. Inserts one active and one resolved alert for the tenant; verifies the filters.

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.alert import AlertRepository


async def _seed_alerts(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:  # owner -> bypasses RLS
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity) "
                "VALUES (:id, :tid, :did, 'device.down', '', 'critical')"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "did": device_id},
        )
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, resolved_at) "
                "VALUES (:id, :tid, :did, 'gateway.down', 'WAN', 'warning', :r)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "did": device_id, "r": datetime.now(timezone.utc)},
        )
        await s.commit()


async def test_list_active_only(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = (
        await _device_id_of(db_engine, "fw-a")
    )
    await _seed_alerts(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        alerts = await AlertRepository(s, tenant_a).list(active_only=True)
    assert [a.type for a in alerts] == ["device.down"]


async def test_list_all(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = await _device_id_of(db_engine, "fw-a")
    await _seed_alerts(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        alerts = await AlertRepository(s, tenant_a).list(active_only=False)
    assert {a.type for a in alerts} == {"device.down", "gateway.down"}


async def _device_id_of(db_engine, name):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        return (
            await s.execute(text("SELECT id FROM devices WHERE name = :n"), {"n": name})
        ).scalar_one()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run: `... .venv/bin/python -m pytest tests/test_alert_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: app.repositories.alert`.

- [ ] **Step 3: Write the alert schema**

Create `app/schemas/alert.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    type: str
    label: str
    severity: str
    opened_at: datetime
    resolved_at: datetime | None
    details: dict

    model_config = {"from_attributes": True}
```

- [ ] **Step 4: Write the alert repository**

Create `app/repositories/alert.py`. Uses the ORM model `Alert` (normal table, not a hypertable). Order: most recent first.

```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert


class AlertRepository:
    """Alert reads for a tenant. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self, *, active_only: bool) -> Sequence[Alert]:
        stmt = select(Alert).where(Alert.tenant_id == self.tenant_id)
        if active_only:
            stmt = stmt.where(Alert.resolved_at.is_(None))
        stmt = stmt.order_by(Alert.opened_at.desc())
        return (await self.session.execute(stmt)).scalars().all()
```

- [ ] **Step 5: Run the repository tests and verify they pass**

Run: `... .venv/bin/python -m pytest tests/test_alert_repository.py -v`
Expected: PASS (2/2).

- [ ] **Step 6: Add the alerts endpoint to the router**

In `app/api/monitoring.py`, add the import and endpoint:

```python
from app.repositories.alert import AlertRepository
from app.schemas.alert import AlertOut
```

```python
@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    tenant_id: uuid.UUID,
    active: bool = Query(True, description="Active alerts only (resolved_at IS NULL)"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    alerts = await AlertRepository(session, tenant_id).list(active_only=active)
    return [AlertOut.model_validate(a) for a in alerts]
```

- [ ] **Step 7: Run the full suite**

Run: `... .venv/bin/python -m pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/alert.py app/repositories/alert.py app/api/monitoring.py tests/test_alert_repository.py
git commit -m "feat(backend): alerts list endpoint (active/historical, tenant-scoped)"
```

---

## Task 4: Fleet health summary endpoint

**Files:**
- Create: `app/schemas/health.py`
- Modify: `app/api/monitoring.py`
- Test: covered in Task 5 (`tests/test_monitoring_api.py`)

- [ ] **Step 1: Write the health schema**

Create `app/schemas/health.py`:

```python
from pydantic import BaseModel


class HealthOut(BaseModel):
    total_devices: int
    by_status: dict[str, int]  # e.g. {"reachable": 3, "unverified": 1}
    active_alerts: int
```

- [ ] **Step 2: Add the health endpoint to the router**

In `app/api/monitoring.py`, add import and endpoint. Count devices by `status` and active alerts, scoped by tenant (application filter + RLS).

```python
from sqlalchemy import func, select

from app.models.alert import Alert
from app.models.device import Device
from app.schemas.health import HealthOut
```

```python
@router.get("/health", response_model=HealthOut)
async def fleet_health(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> HealthOut:
    status_rows = (
        await session.execute(
            select(Device.status, func.count())
            .where(Device.tenant_id == tenant_id)
            .group_by(Device.status)
        )
    ).all()
    by_status = {status: count for status, count in status_rows}
    total = sum(by_status.values())
    active_alerts = (
        await session.execute(
            select(func.count())
            .select_from(Alert)
            .where(Alert.tenant_id == tenant_id, Alert.resolved_at.is_(None))
        )
    ).scalar_one()
    return HealthOut(total_devices=total, by_status=by_status, active_alerts=active_alerts)
```

- [ ] **Step 3: Quick import check (no syntax errors)**

Run: `... .venv/bin/python -c "import app.main"`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add app/schemas/health.py app/api/monitoring.py
git commit -m "feat(backend): fleet health summary endpoint (device counts + active alerts)"
```

---

## Task 5: Endpoint integration tests + RBAC + cross-tenant isolation via API

**Files:**
- Create: `tests/test_monitoring_api.py`
- Create: `tests/test_monitoring_rls_api.py`

These tests close the milestone: endpoint happy-path, RBAC gate, and — most important for security — cross-tenant isolation **through the real API** with `opngms_app` connection.

- [ ] **Step 1: Write happy-path + RBAC tests (owner client)**

Create `tests/test_monitoring_api.py`. Use `api_client` (owner) for the happy path, and a read_only membership to verify that VIEW is granted. For simplicity authenticate a superadmin via `/api/setup` + `/api/login` (sees all tenants), create a tenant and a device, inject metrics/alerts as owner, then query the endpoints.

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from tests.factories import make_tenant


async def _login_superadmin(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return tid


async def _insert_device(db_engine, tenant_id, name="fw1", status="reachable"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices "
                "(id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, :st, '{}')"
            ),
            {"id": did, "t": tenant_id, "n": name, "st": status},
        )
        await s.commit()
    return did


async def test_metrics_endpoint_returns_series(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                "VALUES (:t, :d, 'cpu.load', '', :tid, 42.0)"
            ),
            {"t": datetime.now(timezone.utc), "d": did, "tid": tid},
        )
        await s.commit()
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "cpu.load"
    assert body["points"][0]["value"] == 42.0
    assert body["last"][0]["value"] == 42.0


async def test_health_endpoint_counts(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    await _insert_device(db_engine, tid, name="fw1", status="reachable")
    await _insert_device(db_engine, tid, name="fw2", status="unverified")
    r = await api_client.get(f"/api/tenants/{tid}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["total_devices"] == 2
    assert body["by_status"] == {"reachable": 1, "unverified": 1}
    assert body["active_alerts"] == 0


async def test_alerts_endpoint_active_filter(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity) "
                "VALUES (:id, :tid, :did, 'device.down', '', 'critical')"
            ),
            {"id": uuid.uuid4(), "tid": tid, "did": did},
        )
        await s.commit()
    r = await api_client.get(f"/api/tenants/{tid}/alerts", params={"active": "true"})
    assert r.status_code == 200
    assert [a["type"] for a in r.json()] == ["device.down"]


async def test_metrics_requires_auth(api_client, db_engine):
    tid = await _login_superadmin(api_client, db_engine)
    did = await _insert_device(db_engine, tid)
    # new client without session cookie
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(
            f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
        )
    assert r.status_code == 401
```

- [ ] **Step 2: Run and verify**

Run: `... .venv/bin/python -m pytest tests/test_monitoring_api.py -v`
Expected: PASS (4/4). If an endpoint returns 404 on the tenant, check that `make_tenant`/superadmin login works as in `test_devices_rls_api.py`.

- [ ] **Step 3: Write the cross-tenant isolation test via real API (opngms_app)**

Create `tests/test_monitoring_rls_api.py`. Uses `app_role_api_client` (real `opngms_app` connection → RLS active). Creates two tenants, a device + metrics/alerts for each, and verifies that querying tenant B does **not** show tenant A's data. Reuse the setup helper from `test_devices_rls_api.py`.

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.main import app
from app.services.onboarding import ProbeResult, get_prober
from tests.factories import make_tenant

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _setup(app_role_api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="a")
        b = await make_tenant(s, slug="b")
        await s.commit()
        ta, tb = a.id, b.id
    await app_role_api_client.post(
        "/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"}
    )

    async def _fake(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake
    await app_role_api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    return ta, tb


async def _make_device(app_role_api_client, tid, name):
    r = await app_role_api_client.post(
        f"/api/tenants/{tid}/devices",
        json={"name": name, "base_url": f"https://{name}", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert r.status_code == 201
    return uuid.UUID(r.json()["id"])


async def test_metrics_and_alerts_isolated_via_api(app_role_api_client, db_engine):
    ta, tb = await _setup(app_role_api_client, db_engine)
    dev_a = await _make_device(app_role_api_client, ta, "fw-a")
    dev_b = await _make_device(app_role_api_client, tb, "fw-b")

    # inject data as owner (bypasses RLS) for both
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        for tid, did, val in ((ta, dev_a, 11.0), (tb, dev_b, 22.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": did, "tid": tid, "v": val},
            )
            await s.execute(
                text(
                    "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity) "
                    "VALUES (:id, :tid, :did, 'device.down', '', 'critical')"
                ),
                {"id": uuid.uuid4(), "tid": tid, "did": did},
            )
        await s.commit()

    # tenant A sees only its own data
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/metrics", params={"metric": "cpu.load"}
    )
    assert ra.json()["points"][0]["value"] == 11.0
    # B's data on B's device, queried in A's context -> RLS hides everything
    cross = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_b}/metrics", params={"metric": "cpu.load"}
    )
    assert cross.json()["points"] == []

    aa = await app_role_api_client.get(f"/api/tenants/{ta}/alerts")
    assert [x["device_id"] for x in aa.json()] == [str(dev_a)]
    ab = await app_role_api_client.get(f"/api/tenants/{tb}/alerts")
    assert [x["device_id"] for x in ab.json()] == [str(dev_b)]

    ha = await app_role_api_client.get(f"/api/tenants/{ta}/health")
    assert ha.json()["total_devices"] == 1
    assert ha.json()["active_alerts"] == 1
```

- [ ] **Step 4: Run and verify isolation**

Run: `... .venv/bin/python -m pytest tests/test_monitoring_rls_api.py -v`
Expected: PASS. This test **proves** RLS propagation to Timescale chunks: if `ra.json()["points"]` were empty, the grants were not propagated to chunks → revisit Task 1 Step 5.

- [ ] **Step 5: Run the full suite + `alembic check`**

Run: `... .venv/bin/python -m pytest -q`
Expected: all PASS.
Then re-run the `alembic check` procedure on a clean DB (Task 1 Step 8). Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add tests/test_monitoring_api.py tests/test_monitoring_rls_api.py
git commit -m "test(backend): monitoring endpoint integration + cross-tenant isolation via API"
```

---

## Task 6: Technical debt

Add a "Technical debt" section to the end of this file with items that emerged during implementation. Known items:

- [ ] **Step 1: Record 2C debt**

Append to this plan:

```markdown
## Technical debt (2C)

- **Deferred continuous aggregate `metrics_5m`**: downsampling is on-the-fly (`time_bucket()`).
  At greater scale or for long-period reports (Phase 5), materialise the CAGG + differentiated
  retention (raw 30d, longer CAGG) as per spec §4.1. Re-evaluate in 2D or Phase 5.
- **Metrics endpoint without pagination/limit**: a wide range without `bucket` can return many
  points. Consider a row cap or mandatory `bucket` beyond N days.
- **`bucket` as integer seconds**: the API accepts `bucket` in seconds. If 2D needs "natural"
  buckets (5m/1h/1d aligned), consider an enumerated parameter.
- **Metric names not validated**: `metric` is a free string; an enumerated set/registry of known
  metrics would improve API DX (and enable 422 validation).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase2-milestone2C-metrics-alerts-api.md
git commit -m "docs: technical debt milestone 2C"
```

---

## Definition of "done" (2C)

- RLS protects `metrics` and `alerts` (migration 0007), with grants propagated to Timescale chunks.
- `GET /devices/{id}/metrics` returns series (raw or downsampled) + last value per label.
- `GET /health` returns device counts by status + active alerts.
- `GET /alerts` returns alerts (active or historical).
- All endpoints are tenant-scoped (`require_tenant(DEVICE_VIEW)`) and isolated by RLS — a test via real `opngms_app` connection proves it cross-tenant.
- Green suite + clean `alembic check`.

---

## Technical debt (2C) — consolidated from reviews

**Performance / scale**
- **Deferred continuous aggregate `metrics_5m`**: downsampling is on-the-fly (`time_bucket()`).
  At greater scale or for long-period reports (Phase 5), materialise the CAGG + differentiated
  retention (raw 30d, longer CAGG) as per spec §4.1. Re-evaluate in 2D or Phase 5.
- **Silent truncation of most recent points** (Task 2 review): the series query without `bucket`
  applies `ORDER BY time ASC LIMIT MAX_POINTS` (defensive cap at 5000). If the series exceeds the
  cap, the **oldest** points are returned, truncating the recent tail, with no truncation flag in
  the response. For the dashboard (2D) consider: truncating the oldest instead, exposing a
  `truncated` flag, or making `bucket` mandatory beyond N days.
- **`bucket` as integer seconds**: the API accepts `bucket` in seconds. If 2D needs "natural"
  buckets (5m/1h/1d aligned), consider an enumerated parameter.

**Data model / contract**
- **`alerts.details` open JSONB passthrough** (Task 3 review): `AlertOut.details` exposes the JSONB
  as-is to anyone with `DEVICE_VIEW` (within their own tenant — the cross-tenant boundary is
  guaranteed by RLS). Today no leakage: the poller (`alerting.py`) never writes `details`
  (always `{}`). Write-side governance: BEFORE the poller populates `details`, decide what is
  permitted there (never secrets/PII) and consider a typed whitelisted model instead of open `dict`.
- **ORM↔migration divergence on `alerts.details`** (Task 1 review): the `Alert.details` model has
  `default=dict` (Python) but no `server_default`, while migration 0006 has
  `server_default '{}'::jsonb`. In tests (schema from `create_all`) raw INSERTs must pass
  `details` explicitly. Align the model by adding `server_default` (not detected by
  `alembic check` because `compare_server_default` is not active).
- **Metric names not validated**: `metric` is a free string; an enumerated set/registry of known
  metrics would improve API DX (and enable 422 validation).

**Tests (nice-to-have)**
- **DRY of test seeds** (Task 5 review): the raw INSERT blocks for `metrics`/`alerts` and the
  superadmin/login setup are duplicated across `test_monitoring_api.py`, `test_monitoring_rls_api.py`
  and `test_devices_rls_api.py`. Extract `make_metric`/`make_alert` + login helpers into
  `factories.py`/`conftest.py` when the duplication grows.

# OPNGMS — Fase 2 / Milestone 2C: API Metriche / Salute / Alert (tenant-scoped + RLS) — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Esporre via API REST le metriche serie-temporali, il riassunto di salute della flotta e gli alert che il poller (2A/2B) già scrive, isolati per cliente dalla RLS Postgres.

**Architecture:** Tre endpoint read-only sotto `/api/tenants/{tenant_id}/...`, gated da `require_tenant(DEVICE_VIEW)` + tenant-context (che imposta `app.current_tenant`). La RLS Postgres filtra `metrics` e `alerts` per tenant esattamente come per `devices` (doppio livello: filtro applicativo nel repository + policy DB). Una nuova migrazione estende la RLS alle due tabelle e concede i privilegi a `opngms_app`, incluso il grant esplicito sull'hypertable `metrics` per propagarlo ai chunk TimescaleDB. Il downsampling delle serie lunghe è fatto on-the-fly con `time_bucket()` (la continuous aggregate materializzata è deferita).

**Tech Stack:** FastAPI async, SQLAlchemy 2.0 async + asyncpg, TimescaleDB (hypertable `metrics`), Pydantic v2, pytest + pytest-asyncio.

---

## Contesto per l'implementatore (leggere prima di iniziare)

Sei in una codebase esistente con pattern consolidati. **Seguili esattamente.** Riferimenti chiave:

- **Router tenant-scoped**: `app/api/devices.py` — `APIRouter(prefix="/api/tenants/{tenant_id}/...")`, ogni endpoint dipende da `ctx = Depends(require_tenant(Action.DEVICE_VIEW))` e `session = Depends(get_session)`. `require_tenant` (in `app/core/deps.py`) chiama `tenant_context`, che **imposta `app.current_tenant`** sulla sessione (`set_tenant_context`) → la RLS si attiva. Non devi gestire la RLS nell'endpoint: arriva gratis dal dependency.
- **Repository tenant-scoped**: `app/repositories/device.py` — costruito con `(session, tenant_id)`, ogni query filtra `WHERE tenant_id == self.tenant_id` (filtro applicativo) **in aggiunta** alla RLS DB. Replica questo pattern per metriche e alert.
- **RLS — fonte unica**: `app/core/rls.py`. `TENANT_TABLES` elenca le tabelle con RLS (oggi solo `["devices"]`). `policy_create_statement(table)` genera la `CREATE POLICY tenant_isolation`. La conftest dei test (`tests/conftest.py`, fixture `db_engine`) chiama `enable_rls_statements()` su tutte le `TENANT_TABLES`: **appena aggiungi `metrics`/`alerts` lì, i test le proteggeranno automaticamente.**
- **Ruoli DB**: `app/core/db_roles.py`. Le migrazioni/il poller girano come owner superuser `opngms` (bypassa la RLS — è infrastruttura fidata). L'API si connette come `opngms_app` (NOSUPERUSER NOBYPASSRLS) → la RLS si applica. `grant_app_role_statements()` concede SELECT/INSERT/UPDATE/DELETE `ON ALL TABLES IN SCHEMA public` + default privileges.
- **Modelli**: `app/models/metric.py` (`Metric`: PK composita `time,device_id,metric,label`, + `tenant_id`, `value`), `app/models/alert.py` (`Alert`: `id` PK, `tenant_id`, `device_id`, `type`, `label`, `severity`, `opened_at`, `resolved_at` nullable, `details` JSONB).
- **Schemi Pydantic**: `app/schemas/device.py` — `DeviceOut` usa `model_config = {"from_attributes": True}`. Replica lo stile.
- **Registrazione router**: `app/main.py` — `app.include_router(...)`.
- **RBAC**: `app/core/rbac.py` — `Action.DEVICE_VIEW` è concesso a `tenant_admin/operator/read_only` (giusto per endpoint di sola lettura). Riusalo, **non** creare nuove Action.
- **Test di isolamento**: `tests/test_rls_isolation.py` (raw SELECT con `SET ROLE opngms_app`) e `tests/test_devices_rls_api.py` (via `app_role_api_client`, connessione reale come `opngms_app`). Fixture rilevanti in `tests/conftest.py`: `db_engine`, `two_tenants`, `api_client` (owner), `app_role_api_client` (opngms_app reale). `tests/factories.py` ha `make_tenant`.

**Comando test** (un solo DB, owner+app role nello stesso): dalla dir `backend/`
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Il DB di test gira in Docker (`docker compose ps` → servizio `db`). La suite oggi conta **108 test verdi**.

**Scostamento dallo spec (deliberato, YAGNI):** lo spec §7 prevede di leggere da una *continuous aggregate* `metrics_5m` per i range lunghi. Per l'MVP la **deferiamo**: il downsampling è fatto on-the-fly con `time_bucket()` (stessa shape di risposta, corretto a 100-300 device/30 giorni di retention). La CAGG materializzata + la sua retention restano debito tecnico per un'ottimizzazione futura (Task 6 la registra).

---

## File Structure

| File | Responsabilità | Azione |
|------|----------------|--------|
| `app/core/rls.py` | Aggiungere `metrics`, `alerts` a `TENANT_TABLES` | Modify |
| `migrations/versions/0007_rls_metrics_alerts.py` | Enable RLS+policy su metrics/alerts + grant a opngms_app | Create |
| `app/schemas/metric.py` | `MetricPoint`, `MetricSeriesOut` | Create |
| `app/schemas/alert.py` | `AlertOut` | Create |
| `app/schemas/health.py` | `HealthOut` | Create |
| `app/repositories/metric.py` | `MetricRepository` (serie con time_bucket, ultimo valore) | Create |
| `app/repositories/alert.py` | `AlertRepository` (lista attivi/storici) | Create |
| `app/api/monitoring.py` | Router: metrics series, health, alerts | Create |
| `app/main.py` | `include_router(monitoring_router)` | Modify |
| `tests/test_rls_isolation.py` | Estendere: metrics/alerts in TENANT_TABLES + isolamento raw | Modify |
| `tests/test_metric_repository.py` | Serie + ultimo valore, tenant-scoped | Create |
| `tests/test_alert_repository.py` | Lista alert attivi/tutti, tenant-scoped | Create |
| `tests/test_monitoring_api.py` | Endpoint happy-path + RBAC (owner client) | Create |
| `tests/test_monitoring_rls_api.py` | Isolamento cross-tenant via opngms_app reale | Create |

---

## Task 1: Estendere la RLS a `metrics` e `alerts`

**Files:**
- Modify: `app/core/rls.py:7`
- Create: `migrations/versions/0007_rls_metrics_alerts.py`
- Modify: `tests/test_rls_isolation.py`

Questa è la fondazione di sicurezza: senza RLS sulle due tabelle, qualunque endpoint potrebbe far trapelare metriche/alert cross-tenant. Procediamo TDD partendo dal contratto statico, poi la migrazione, poi l'isolamento reale.

- [ ] **Step 1: Scrivere il test che fallisce (contratto statico)**

In `tests/test_rls_isolation.py`, modificare `test_rls_statements_cover_devices` aggiungendo subito dopo una nuova funzione:

```python
def test_rls_statements_cover_metrics_and_alerts():
    assert "metrics" in TENANT_TABLES
    assert "alerts" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    for table in ("metrics", "alerts"):
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
```

- [ ] **Step 2: Eseguire il test e verificarne il fallimento**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py::test_rls_statements_cover_metrics_and_alerts -v`
Expected: FAIL con `assert 'metrics' in ['devices']`.

- [ ] **Step 3: Aggiungere le tabelle a `TENANT_TABLES`**

In `app/core/rls.py`, riga 7:

```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts"]
```

- [ ] **Step 4: Eseguire il test e verificarne il passaggio**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py::test_rls_statements_cover_metrics_and_alerts -v`
Expected: PASS.

- [ ] **Step 5: Scrivere la migrazione 0007**

Crea `migrations/versions/0007_rls_metrics_alerts.py`. Abilita RLS+policy SOLO sulle due nuove tabelle (`devices` è già coperta da 0002/0003) e ri-concede i privilegi a `opngms_app` (ora che le tabelle esistono), con grant esplicito su `metrics` perché TimescaleDB propaghi il privilegio ai chunk.

```python
"""RLS su metrics + alerts; grant a opngms_app (con propagazione ai chunk Timescale)"""

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
    # Le tabelle metrics/alerts sono state create DOPO il GRANT ON ALL TABLES della 0003:
    # ri-eseguiamo i grant ora che esistono. Su `metrics` (hypertable) il GRANT esplicito
    # fa propagare il privilegio ai chunk TimescaleDB (esistenti e futuri).
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

- [ ] **Step 6: Aggiungere il test di isolamento raw su metrics e alerts**

In `tests/test_rls_isolation.py`, aggiungere in fondo. Inserisce una metrica e un alert per ciascun tenant (come owner, che bypassa la RLS), poi legge come `opngms_app` reale verificando l'isolamento. Riusa il pattern di `test_app_role_connection_enforces_rls`.

```python
async def test_metrics_alerts_isolated_cross_tenant(db_engine, two_tenants):
    """metrics e alerts: la connessione reale opngms_app vede solo il tenant in contesto.

    Prova anche la propagazione della RLS ai chunk dell'hypertable Timescale.
    """
    import os
    import uuid as _uuid
    from datetime import datetime, timezone

    tenant_a, tenant_b = two_tenants
    # device_id qualunque: la RLS filtra su tenant_id, non serve un device reale per la metrica.
    owner_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner_factory() as s:  # owner = superuser -> bypassa RLS, inserisce per entrambi
        for tid, val in ((tenant_a, 1.0), (tenant_b, 2.0)):
            await s.execute(
                text(
                    "INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                    "VALUES (:t, :d, 'cpu.load', '', :tid, :v)"
                ),
                {"t": datetime.now(timezone.utc), "d": _uuid.uuid4(), "tid": tid, "v": val},
            )
        # alert: device_id deve riferire un device esistente (FK). two_tenants ha fw-a/fw-b.
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
            # nessun contesto -> fail-closed su entrambe
            assert (await s2.execute(text("SELECT value FROM metrics"))).scalars().all() == []
            assert (await s2.execute(text("SELECT id FROM alerts"))).scalars().all() == []
    finally:
        await engine.dispose()
```

- [ ] **Step 7: Eseguire l'intera suite RLS**

Run: `... .venv/bin/python -m pytest tests/test_rls_isolation.py -v`
Expected: tutti PASS (incluso il nuovo isolamento metrics/alerts). Se `test_metrics_alerts_isolated_cross_tenant` mostra che `opngms_app` vede 0 righe **anche con contesto** su `metrics`, è il problema di propagazione grant→chunk: verificare che lo Step 5 abbia eseguito `GRANT SELECT ON metrics`. La conftest concede già via `grant_app_role_statements()` prima degli insert, quindi i chunk nuovi ereditano.

- [ ] **Step 8: Verificare `alembic check` su DB pulito**

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
Expected: `upgrade head` arriva a 0007; `alembic check` → "No new upgrade operations detected." (le policy/grant non sono oggetti del modello, quindi nessun drift).

- [ ] **Step 9: Commit**

```bash
git add app/core/rls.py migrations/versions/0007_rls_metrics_alerts.py tests/test_rls_isolation.py
git commit -m "feat(backend): RLS su metrics+alerts (migrazione 0007 + isolamento cross-tenant)"
```

---

## Task 2: Repository + schema + endpoint serie metriche

**Files:**
- Create: `app/schemas/metric.py`
- Create: `app/repositories/metric.py`
- Create: `app/api/monitoring.py`
- Modify: `app/main.py`
- Create: `tests/test_metric_repository.py`

- [ ] **Step 1: Scrivere il test del repository che fallisce**

Crea `tests/test_metric_repository.py`. Inserisce alcune metriche per il tenant attivo (come owner) e verifica che il repository, sotto `SET ROLE opngms_app` + contesto, ritorni serie e ultimo valore corretti.

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
    async with factory() as s:  # owner -> bypassa RLS
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
            timedelta(hours=1),  # un bucket -> media (10+20+30)/3 = 20
        )
    assert len(points) == 1
    assert points[0].value == 20.0
```

- [ ] **Step 2: Eseguire i test e verificarne il fallimento**

Run: `... .venv/bin/python -m pytest tests/test_metric_repository.py -v`
Expected: FAIL con `ModuleNotFoundError: app.repositories.metric` / `app.schemas.metric`.

- [ ] **Step 3: Scrivere lo schema metriche**

Crea `app/schemas/metric.py`:

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
    last: list[MetricPoint]  # ultimo valore per label
```

- [ ] **Step 4: Scrivere il repository metriche**

Crea `app/repositories/metric.py`. Filtro applicativo `tenant_id` + `device_id` (doppio isolamento con la RLS). `series` con `time_bucket` opzionale; `last` = ultimo punto per label via `DISTINCT ON`.

```python
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.metric import MetricPoint


class MetricRepository:
    """Letture serie-temporali per tenant. Doppio isolamento: filtro tenant_id + RLS."""

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

- [ ] **Step 5: Eseguire i test del repository e verificarne il passaggio**

Run: `... .venv/bin/python -m pytest tests/test_metric_repository.py -v`
Expected: PASS (3/3).

- [ ] **Step 6: Scrivere il router con l'endpoint serie + registrarlo**

Crea `app/api/monitoring.py`. Parsing dei query param `metric` (obbligatorio), `from`/`to` (default: ultime 24h), `bucket` (ISO-8601 secondi opzionale → `timedelta`). Restituisce `MetricSeriesOut`.

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
    metric: str = Query(..., description="Nome metrica, es. 'cpu.load'"),
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

In `app/main.py`, aggiungere l'import e la registrazione accanto agli altri router:

```python
from app.api.monitoring import router as monitoring_router
```
e dopo `app.include_router(me_tenants_router)`:
```python
app.include_router(monitoring_router)
```

- [ ] **Step 7: Eseguire l'intera suite**

Run: `... .venv/bin/python -m pytest -q`
Expected: tutti PASS (108 + i nuovi del repository). L'endpoint sarà testato in Task 5.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/metric.py app/repositories/metric.py app/api/monitoring.py app/main.py tests/test_metric_repository.py
git commit -m "feat(backend): endpoint serie metriche (repository time_bucket + ultimo valore)"
```

---

## Task 3: Repository + schema + endpoint alert

**Files:**
- Create: `app/schemas/alert.py`
- Create: `app/repositories/alert.py`
- Modify: `app/api/monitoring.py`
- Create: `tests/test_alert_repository.py`

- [ ] **Step 1: Scrivere il test del repository che fallisce**

Crea `tests/test_alert_repository.py`. Inserisce un alert attivo e uno risolto per il tenant; verifica i filtri.

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
    async with factory() as s:  # owner -> bypassa RLS
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

- [ ] **Step 2: Eseguire i test e verificarne il fallimento**

Run: `... .venv/bin/python -m pytest tests/test_alert_repository.py -v`
Expected: FAIL con `ModuleNotFoundError: app.repositories.alert`.

- [ ] **Step 3: Scrivere lo schema alert**

Crea `app/schemas/alert.py`:

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

- [ ] **Step 4: Scrivere il repository alert**

Crea `app/repositories/alert.py`. Usa il modello ORM `Alert` (tabella normale, non hypertable). Ordine: più recenti prima.

```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert


class AlertRepository:
    """Letture alert per tenant. Doppio isolamento: filtro tenant_id + RLS."""

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

- [ ] **Step 5: Eseguire i test del repository e verificarne il passaggio**

Run: `... .venv/bin/python -m pytest tests/test_alert_repository.py -v`
Expected: PASS (2/2).

- [ ] **Step 6: Aggiungere l'endpoint alert al router**

In `app/api/monitoring.py`, aggiungere l'import e l'endpoint:

```python
from app.repositories.alert import AlertRepository
from app.schemas.alert import AlertOut
```

```python
@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    tenant_id: uuid.UUID,
    active: bool = Query(True, description="Solo alert attivi (resolved_at IS NULL)"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[AlertOut]:
    alerts = await AlertRepository(session, tenant_id).list(active_only=active)
    return [AlertOut.model_validate(a) for a in alerts]
```

- [ ] **Step 7: Eseguire l'intera suite**

Run: `... .venv/bin/python -m pytest -q`
Expected: tutti PASS.

- [ ] **Step 8: Commit**

```bash
git add app/schemas/alert.py app/repositories/alert.py app/api/monitoring.py tests/test_alert_repository.py
git commit -m "feat(backend): endpoint lista alert (attivi/storici, tenant-scoped)"
```

---

## Task 4: Endpoint riassunto salute flotta

**Files:**
- Create: `app/schemas/health.py`
- Modify: `app/api/monitoring.py`
- Test: coperto in Task 5 (`tests/test_monitoring_api.py`)

- [ ] **Step 1: Scrivere lo schema health**

Crea `app/schemas/health.py`:

```python
from pydantic import BaseModel


class HealthOut(BaseModel):
    total_devices: int
    by_status: dict[str, int]  # es. {"reachable": 3, "unverified": 1}
    active_alerts: int
```

- [ ] **Step 2: Aggiungere l'endpoint health al router**

In `app/api/monitoring.py`, aggiungere import e endpoint. Conta i device per `status` e gli alert attivi, scoping per tenant (filtro applicativo + RLS).

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

- [ ] **Step 3: Verifica rapida di import (no errori di sintassi)**

Run: `... .venv/bin/python -c "import app.main"`
Expected: nessun errore.

- [ ] **Step 4: Commit**

```bash
git add app/schemas/health.py app/api/monitoring.py
git commit -m "feat(backend): endpoint riassunto salute flotta (conteggi device + alert attivi)"
```

---

## Task 5: Test integrazione endpoint + RBAC + isolamento cross-tenant via API

**Files:**
- Create: `tests/test_monitoring_api.py`
- Create: `tests/test_monitoring_rls_api.py`

Questi test chiudono la milestone: happy-path degli endpoint, gate RBAC, e — il più importante per la sicurezza — l'isolamento cross-tenant **attraverso l'API reale** con connessione `opngms_app`.

- [ ] **Step 1: Scrivere i test happy-path + RBAC (client owner)**

Crea `tests/test_monitoring_api.py`. Usa `api_client` (owner) per il percorso felice, e un membership read_only per verificare che VIEW è concesso. Riusa l'helper di login da `test_devices_rls_api.py` adattandolo. Per semplicità autentichiamo un superadmin via `/api/setup` + `/api/login` (vede tutti i tenant), creiamo un tenant e un device, iniettiamo metriche/alert come owner, poi interroghiamo gli endpoint.

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
    # nuovo client senza cookie di sessione
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        r = await anon.get(
            f"/api/tenants/{tid}/devices/{did}/metrics", params={"metric": "cpu.load"}
        )
    assert r.status_code == 401
```

- [ ] **Step 2: Eseguire e verificare**

Run: `... .venv/bin/python -m pytest tests/test_monitoring_api.py -v`
Expected: PASS (4/4). Se un endpoint dà 404 sul tenant, controlla che `make_tenant`/login superadmin funzioni come in `test_devices_rls_api.py`.

- [ ] **Step 3: Scrivere il test di isolamento cross-tenant via API reale (opngms_app)**

Crea `tests/test_monitoring_rls_api.py`. Usa `app_role_api_client` (connessione reale `opngms_app` → RLS attiva). Crea due tenant, un device + metriche/alert per ciascuno, e verifica che interrogando il tenant B **non** si vedano i dati del tenant A. Riusa l'helper di setup di `test_devices_rls_api.py`.

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

    # inietta dati come owner (bypassa RLS) per entrambi
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

    # tenant A vede solo i propri dati
    ra = await app_role_api_client.get(
        f"/api/tenants/{ta}/devices/{dev_a}/metrics", params={"metric": "cpu.load"}
    )
    assert ra.json()["points"][0]["value"] == 11.0
    # i dati di B sul device di B, interrogati nel contesto di A -> RLS nasconde tutto
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

- [ ] **Step 4: Eseguire e verificare l'isolamento**

Run: `... .venv/bin/python -m pytest tests/test_monitoring_rls_api.py -v`
Expected: PASS. Questo test **dimostra** la propagazione della RLS ai chunk Timescale: se `ra.json()["points"]` fosse vuoto, i grant non sono propagati ai chunk → rivedere Task 1 Step 5.

- [ ] **Step 5: Eseguire l'intera suite + `alembic check`**

Run: `... .venv/bin/python -m pytest -q`
Expected: tutti PASS.
Poi rieseguire la procedura `alembic check` su DB pulito (Task 1 Step 8). Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add tests/test_monitoring_api.py tests/test_monitoring_rls_api.py
git commit -m "test(backend): integrazione endpoint monitoraggio + isolamento cross-tenant via API"
```

---

## Task 6: Debito tecnico

Aggiungere in fondo a questo file la sezione "Debito tecnico" con le voci emerse durante l'implementazione. Voci note in partenza:

- [ ] **Step 1: Registrare il debito 2C**

Append a questo piano:

```markdown
## Debito tecnico (2C)

- **Continuous aggregate `metrics_5m` deferita**: il downsampling è on-the-fly (`time_bucket()`).
  A scala maggiore o per i report a lungo periodo (Fase 5), materializzare la CAGG + retention
  differenziata (raw 30g, CAGG più lunga) come da spec §4.1. Da rivalutare in 2D o Fase 5.
- **Endpoint metriche senza paginazione/limite**: un range ampio senza `bucket` può restituire
  molti punti. Valutare un cap di righe o `bucket` obbligatorio oltre N giorni.
- **`bucket` come secondi interi**: l'API accetta `bucket` in secondi. Se la 2D necessita di
  bucket "naturali" (5m/1h/1d allineati), valutare un parametro enumerato.
- **Nomi metrica non validati**: `metric` è una stringa libera; un set enumerato/registro delle
  metriche note migliorerebbe la DX dell'API (e abiliterebbe validazione 422).
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase2-milestone2C-metrics-alerts-api.md
git commit -m "docs: debito tecnico milestone 2C"
```

---

## Definizione di "fatto" (2C)

- La RLS protegge `metrics` e `alerts` (migrazione 0007), con grant propagati ai chunk Timescale.
- `GET /devices/{id}/metrics` ritorna serie (raw o downsampled) + ultimo valore per label.
- `GET /health` ritorna conteggi device per stato + alert attivi.
- `GET /alerts` ritorna gli alert (attivi o storici).
- Tutti gli endpoint sono tenant-scoped (`require_tenant(DEVICE_VIEW)`) e isolati dalla RLS — un test via connessione `opngms_app` reale lo dimostra cross-tenant.
- Suite verde + `alembic check` pulito.

---

## Debito tecnico (2C) — consolidato dalle review

**Performance / scala**
- **Continuous aggregate `metrics_5m` deferita**: il downsampling è on-the-fly (`time_bucket()`).
  A scala maggiore o per i report a lungo periodo (Fase 5), materializzare la CAGG + retention
  differenziata (raw 30g, CAGG più lunga) come da spec §4.1. Da rivalutare in 2D o Fase 5.
- **Troncamento silenzioso dei punti più recenti** (review Task 2): la query serie senza `bucket`
  applica `ORDER BY time ASC LIMIT MAX_POINTS` (cap difensivo a 5000). Se la serie supera il cap,
  vengono restituiti i punti **più vecchi**, troncando la coda recente, senza alcun flag di
  troncamento nella risposta. Per la dashboard (2D) valutare: troncare i più recenti invece dei
  più vecchi, o esporre un flag `truncated`, o rendere `bucket` obbligatorio oltre N giorni.
- **`bucket` come secondi interi**: l'API accetta `bucket` in secondi. Se la 2D necessita di
  bucket "naturali" (5m/1h/1d allineati), valutare un parametro enumerato.

**Modello dati / contratto**
- **`alerts.details` passthrough JSONB aperto** (review Task 3): `AlertOut.details` espone il JSONB
  così com'è a chiunque abbia `DEVICE_VIEW` (entro il proprio tenant — la frontiera cross-tenant è
  garantita dalla RLS). Oggi nessun leak: il poller (`alerting.py`) non scrive mai `details`
  (sempre `{}`). Governance lato-write: PRIMA che il poller popoli `details`, decidere cosa è lecito
  scrivervi (mai segreti/PII) e valutare un modello tipizzato con whitelist invece di `dict` aperto.
- **Divergenza ORM↔migrazione su `alerts.details`** (review Task 1): il modello `Alert.details` ha
  `default=dict` (Python) ma niente `server_default`, mentre la migrazione 0006 ha
  `server_default '{}'::jsonb`. In test (schema da `create_all`) gli INSERT raw devono passare
  `details` esplicito. Allineare il modello aggiungendo `server_default` (non rilevato da
  `alembic check` perché `compare_server_default` non è attivo).
- **Nomi metrica non validati**: `metric` è una stringa libera; un set enumerato/registro delle
  metriche note migliorerebbe la DX dell'API (e abiliterebbe validazione 422).

**Test (nice-to-have)**
- **DRY dei seed di test** (review Task 5): i blocchi di INSERT raw per `metrics`/`alerts` e il
  setup superadmin/login sono duplicati tra `test_monitoring_api.py`, `test_monitoring_rls_api.py`
  e `test_devices_rls_api.py`. Estrarre `make_metric`/`make_alert` + helper login in
  `factories.py`/`conftest.py` quando il duplicato cresce.

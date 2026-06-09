# OPNGMS Fase 2 · Milestone 2A — Infra + Storage + Poller Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Far fluire le metriche di salute essenziale (up/down, CPU/mem/disco, uptime, firmware) di ciascun device dentro una hypertable TimescaleDB, raccolte da un worker ARQ su cadenza, con aggiornamento dello stato del device.

**Architecture:** Postgres diventa **TimescaleDB** (estensione); le metriche vivono nell'hypertable `metrics`. Un **worker ARQ** (processo `python -m app.worker`, connesso come owner — bypassa la RLS) ogni N secondi accoda un `poll_device(id)` per device; i worker caricano il device, decifrano i segreti, interrogano via `OpnsenseClient.get_system_info()`+`get_firmware_status()`, scrivono le metriche e aggiornano `Device.status/last_seen/firmware_version`. La logica di raccolta è in un service `collect_and_store(session, device, client)` iniettabile/testabile a parte dall'orchestrazione ARQ.

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy async, **TimescaleDB** (Postgres+estensione), **ARQ + Redis**, httpx, pytest + respx.

---

## Riferimento spec
Implementa le sez. 4-6, 9(2A) dello spec `docs/superpowers/specs/2026-06-09-opngms-phase2-monitoring-design.md` (storage metriche, poller, connector system-info). La RLS sull'hypertable, le metriche di rete, l'alerting e l'API sono milestone 2B/2C.

## Decisioni di sequenziamento
- **RLS sulla hypertable `metrics`: rimandata alla 2C** (dove arriva l'API di lettura + il test di isolamento). In 2A `metrics` è una hypertable semplice; il poller scrive come owner. Non aggiungere `metrics` a `TENANT_TABLES` in questa milestone.
- **Timestamp per ciclo:** il poller usa UN `now` per device-poll-cycle → tutte le righe di quel ciclo condividono `time`, con `(metric,label)` distinti → la PK `(time, device_id, metric, label)` regge senza collisioni.

## Struttura file
```
backend/
  docker-compose.yml          # MODIFY: image TimescaleDB, servizi redis + worker
  pyproject.toml              # MODIFY: arq, redis
  .env.example                # MODIFY: REDIS_URL, POLL_INTERVAL_SECONDS, ADMIN_DATABASE_URL
  app/
    core/config.py            # MODIFY: redis_url, poll_interval_seconds, admin_database_url
    models/metric.py          # NEW: Metric (hypertable, PK composita)
    models/__init__.py        # MODIFY: export Metric
    connectors/opnsense/client.py  # MODIFY: get_system_info()
    services/monitoring.py    # NEW: collect_and_store(session, device, client)
    worker.py                 # NEW: ARQ WorkerSettings + poll_device + enqueue_device_polls
  migrations/versions/0005_timescale_metrics.py  # NEW
  tests/
    conftest.py               # MODIFY: extension timescaledb + create_hypertable nel DB di test
    test_metric_model.py
    test_connector_system_info.py
    test_monitoring.py
    test_worker_config.py
```

---

## Task 1: Infra — TimescaleDB + Redis + worker nel compose + deps

**Files:** Modify `backend/docker-compose.yml`, `backend/pyproject.toml`, `backend/.env.example`, `backend/app/core/config.py`

- [ ] **Step 1: docker-compose** — in `backend/docker-compose.yml` cambia l'immagine del servizio `db` e aggiungi `redis` + `worker`:
```yaml
services:
  db:
    image: timescale/timescaledb:2.17.2-pg16
    environment:
      POSTGRES_USER: opngms
      POSTGRES_PASSWORD: opngms
      POSTGRES_DB: opngms
    ports:
      - "5432:5432"
    volumes:
      - opngms_pg:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U opngms"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  opngms_pg:
```
(Il servizio `worker` di produzione lo aggiungeremo quando il worker esiste, in fondo a questa milestone — per ora bastano db+redis per sviluppo/test.)

- [ ] **Step 2: deps** — in `backend/pyproject.toml` aggiungi a `[project.dependencies]`: `"arq>=0.26"`, `"redis>=5.0"`. Poi `cd backend && .venv/bin/pip install -e ".[dev]"`.

- [ ] **Step 3: config** — in `backend/app/core/config.py` aggiungi a `Settings`:
```python
    admin_database_url: str | None = None  # owner, per il worker (bypassa RLS)
    redis_url: str = "redis://localhost:6379"
    poll_interval_seconds: int = 60
```

- [ ] **Step 4: .env.example** — aggiungi:
```bash
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
REDIS_URL=redis://localhost:6379
POLL_INTERVAL_SECONDS=60
```
Aggiungi `REDIS_URL`/`ADMIN_DATABASE_URL` ai default di `conftest.py` se non presenti (per i test del worker), via `os.environ.setdefault` in cima alla conftest.

- [ ] **Step 5: recreate Postgres con TimescaleDB + verifica**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
docker compose down
docker compose up -d db redis
# Attendi healthy:
docker compose ps
# Verifica l'estensione disponibile + crea su entrambi i DB:
docker compose exec -T db psql -U opngms -d opngms -c "CREATE EXTENSION IF NOT EXISTS timescaledb; SELECT extversion FROM pg_extension WHERE extname='timescaledb';"
docker compose exec -T db psql -U opngms -c "CREATE DATABASE opngms_test;" || true
docker compose exec -T db psql -U opngms -d opngms_test -c "CREATE EXTENSION IF NOT EXISTS timescaledb;"
docker compose exec -T redis redis-cli ping
```
Expected: `timescaledb` extension version printed; `redis-cli ping` → `PONG`.
⚠️ Se il container `db` NON parte sul volume esistente (creato da postgres:16), il fix è ricreare il volume (dati di sviluppo, non preziosi): `docker compose down -v && docker compose up -d db redis && make createtestdb` e poi ri-applicare le migrazioni esistenti (`ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic upgrade head`). REPORT se è stato necessario.

- [ ] **Step 6: commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/docker-compose.yml backend/pyproject.toml backend/.env.example backend/app/core/config.py backend/tests/conftest.py
git commit -m "chore(backend): TimescaleDB + Redis nel compose, deps arq/redis, config worker"
```

---

## Task 2: Connector `get_system_info`

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Create `backend/tests/test_connector_system_info.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_connector_system_info.py`:
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://203.0.113.10"
SYS_URL = f"{BASE}/api/diagnostics/system/systemInformation"


@respx.mock
async def test_get_system_info_parses_metrics():
    respx.get(SYS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "cpu": {"used": 12.5},
                "memory": {"used_pct": 41.0},
                "disk": {"used_pct": 23.0},
                "uptime_seconds": 86400,
            },
        )
    )
    info = await OpnsenseClient(BASE, "k", "s").get_system_info()
    assert info["cpu_pct"] == 12.5
    assert info["mem_pct"] == 41.0
    assert info["disk_pct"] == 23.0
    assert info["uptime_seconds"] == 86400
```
Run: `cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_system_info.py -v` → FAIL.

- [ ] **Step 2: Implement** — add to `OpnsenseClient` in `backend/app/connectors/opnsense/client.py`:
```python
    async def get_system_info(self) -> dict:
        """CPU/mem/disco/uptime. NOTA: endpoint+campi DA VERIFICARE su un OPNsense reale."""
        data = await self._get("diagnostics/system/systemInformation")
        return {
            "cpu_pct": float((data.get("cpu") or {}).get("used", 0.0)),
            "mem_pct": float((data.get("memory") or {}).get("used_pct", 0.0)),
            "disk_pct": float((data.get("disk") or {}).get("used_pct", 0.0)),
            "uptime_seconds": int(data.get("uptime_seconds", 0)),
        }
```
(The defensive `.get(...)` mapping isolates us from the exact-shape uncertainty; the test pins the contract our code depends on.)

- [ ] **Step 3: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend && .venv/bin/python -m pytest tests/test_connector_system_info.py -v
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_system_info.py
git commit -m "feat(backend): OpnsenseClient.get_system_info (cpu/mem/disco/uptime)"
```
Expected: PASS.

---

## Task 3: Modello `Metric` + migrazione 0005 (hypertable)

**Files:** Create `backend/app/models/metric.py`, `backend/migrations/versions/0005_timescale_metrics.py`; Modify `backend/app/models/__init__.py`, `backend/tests/conftest.py`; Create `backend/tests/test_metric_model.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_metric_model.py`:
```python
from app.models import Base
from app.models.metric import Metric


def test_metric_table_registered():
    assert "metrics" in Base.metadata.tables
    cols = {c.name for c in Metric.__table__.columns}
    assert {"time", "device_id", "tenant_id", "metric", "label", "value"} <= cols
```
Run: `cd backend && .venv/bin/python -m pytest tests/test_metric_model.py -v` → FAIL.

- [ ] **Step 2: Model** — `backend/app/models/metric.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Metric(Base):
    __tablename__ = "metrics"

    # PK composita che INCLUDE la colonna di partizionamento `time` (richiesto da Timescale).
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    metric: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, primary_key=True, default="")
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    value: Mapped[float] = mapped_column(Float)
```
Add to `backend/app/models/__init__.py`: import `Metric`, add to `__all__`.

- [ ] **Step 3: Migration** — `backend/migrations/versions/0005_timescale_metrics.py`:
```python
"""TimescaleDB: estensione + hypertable metrics + retention"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.create_table(
        "metrics",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False, server_default=""),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("time", "device_id", "metric", "label"),
    )
    op.execute("SELECT create_hypertable('metrics', 'time')")
    op.create_index(
        "ix_metrics_tenant_device_metric_time",
        "metrics",
        ["tenant_id", "device_id", "metric", sa.text("time DESC")],
    )
    # Retention: droppa i raw oltre N giorni (default 30; configurabile in seguito).
    op.execute("SELECT add_retention_policy('metrics', INTERVAL '30 days')")


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('metrics', if_exists => true)")
    op.drop_table("metrics")
```
NOTE: `alembic check` non sarà "pulito" su `metrics` perché autogenerate non conosce `create_hypertable`/retention (sono chiamate funzione, non DDL che il modello rappresenta). NON aggiungere `metrics` all'autogenerate-compare se causa rumore; va bene che la migrazione 0005 sia hand-written e che il modello rappresenti solo la tabella base. Verifica invece a runtime (Step 5) che la hypertable esista.

- [ ] **Step 4: conftest — extension + hypertable nel DB di test** — in `backend/tests/conftest.py`, nel fixture `db_engine`, DOPO `create_all` e PRIMA delle altre statement, assicurati l'estensione e converti `metrics` in hypertable (read the file; integrate):
```python
        # TimescaleDB: estensione + converti metrics in hypertable (per i test che scrivono metriche)
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await conn.run_sync(Base.metadata.drop_all)   # (già presente)
        await conn.run_sync(Base.metadata.create_all) # (già presente — crea anche metrics)
        await conn.execute(text("SELECT create_hypertable('metrics', 'time', if_not_exists => true)"))
```
IMPORTANT: l'estensione va creata PRIMA di `create_hypertable`. Metti `CREATE EXTENSION` prima del blocco create_all/hypertable. Se `create_extension` richiede privilegi, l'owner `opngms` (superuser) li ha. Adatta l'ordine reale delle statement nella conftest e REPORT.

- [ ] **Step 5: Apply migration + verify hypertable + tests**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms .venv/bin/alembic upgrade head
docker compose exec -T db psql -U opngms -d opngms -c "SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_name='metrics';"
.venv/bin/python -m pytest tests/test_metric_model.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
```
Expected: hypertable `metrics` listed; model test passes; full suite green (the conftest hypertable conversion must not break existing tests).

- [ ] **Step 6: commit**
```bash
git add backend/app/models/metric.py backend/app/models/__init__.py backend/migrations/versions/0005_timescale_metrics.py backend/tests/conftest.py backend/tests/test_metric_model.py
git commit -m "feat(backend): hypertable metrics TimescaleDB (modello + migrazione 0005)"
```

---

## Task 4: Service `collect_and_store` (raccolta + scrittura metriche + update stato)

**Files:** Create `backend/app/services/monitoring.py`, `backend/tests/test_monitoring.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_monitoring.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.metric import Metric
from app.services.monitoring import collect_and_store


class FakeClient:
    async def get_system_info(self):
        return {"cpu_pct": 10.0, "mem_pct": 50.0, "disk_pct": 20.0, "uptime_seconds": 3600}

    async def get_firmware_status(self):
        return {"product_version": "24.7"}

    async def test_connection(self):
        return "24.7"


async def _make_device(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    device_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'A','a','active')"),
            {"id": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"
            ),
            {"id": device_id, "t": tenant_id},
        )
        await s.commit()
    return tenant_id, device_id


async def test_collect_and_store_writes_metrics_and_updates_status(db_engine):
    tenant_id, device_id = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, device_id)
        await collect_and_store(s, device, FakeClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(Metric).where(Metric.device_id == device_id))).scalars().all()
        by_metric = {r.metric: r.value for r in rows}
        assert by_metric["cpu.pct"] == 10.0
        assert by_metric["mem.pct"] == 50.0
        assert by_metric["disk.pct"] == 20.0
        assert all(r.tenant_id == tenant_id for r in rows)
        device = await s.get(Device, device_id)
        assert device.status == "reachable"
        assert device.firmware_version == "24.7"
        assert device.last_seen is not None
```
Run → FAIL.

- [ ] **Step 2: Implement** — `backend/app/services/monitoring.py`:
```python
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.metric import Metric


def _metric(now, device, name, value, label=""):
    return Metric(
        time=now,
        device_id=device.id,
        tenant_id=device.tenant_id,
        metric=name,
        label=label,
        value=float(value),
    )


async def collect_and_store(
    session: AsyncSession, device: Device, client, now: datetime
) -> None:
    """Pollla un device, scrive le metriche di salute, aggiorna lo stato.

    Non solleva sugli errori del connector: marca il device 'unverified' (la rete
    irraggiungibile non deve far fallire l'intero ciclo). `client` è iniettabile (test).
    """
    try:
        info = await client.get_system_info()
        fw = await client.get_firmware_status()
    except OpnsenseError:
        device.status = "unverified"
        return
    session.add_all(
        [
            _metric(now, device, "cpu.pct", info["cpu_pct"]),
            _metric(now, device, "mem.pct", info["mem_pct"]),
            _metric(now, device, "disk.pct", info["disk_pct"]),
            _metric(now, device, "uptime.seconds", info["uptime_seconds"]),
        ]
    )
    device.status = "reachable"
    device.last_seen = now
    version = fw.get("product_version")
    if version:
        device.firmware_version = version
    await session.flush()
```

- [ ] **Step 3: Run + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_monitoring.py -v
git add backend/app/services/monitoring.py backend/tests/test_monitoring.py
git commit -m "feat(backend): collect_and_store (metriche salute + update stato device)"
```
Expected: PASS.

---

## Task 5: Worker ARQ (`poll_device` + cron enqueue) + servizio worker compose

**Files:** Create `backend/app/worker.py`, `backend/tests/test_worker_config.py`; Modify `backend/docker-compose.yml`

- [ ] **Step 1: Failing test** — `backend/tests/test_worker_config.py`:
```python
from app.worker import WorkerSettings, enqueue_device_polls, poll_device


def test_worker_settings_register_functions_and_cron():
    fn_names = {getattr(f, "__name__", getattr(f, "name", "")) for f in WorkerSettings.functions}
    assert "poll_device" in fn_names
    assert WorkerSettings.cron_jobs  # almeno un cron job (enqueue)
    assert callable(poll_device) and callable(enqueue_device_polls)
```
Run → FAIL.

- [ ] **Step 2: Implement** — `backend/app/worker.py`:
```python
import uuid
from datetime import datetime, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.core.config import get_settings
from app.models.device import Device
from app.services.monitoring import collect_and_store


def _owner_url() -> str:
    s = get_settings()
    return s.admin_database_url or s.database_url


async def enqueue_device_polls(ctx: dict) -> int:
    """Cron: accoda un poll_device per ogni device. Ritorna il numero accodato."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("poll_device", str(device_id))
    return len(ids)


async def poll_device(ctx: dict, device_id: str) -> str:
    """Job: pollla un singolo device e salva metriche+stato."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return "missing"
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        await collect_and_store(session, device, client, now=datetime.now(timezone.utc))
        await session.commit()
        return device.status


async def on_startup(ctx: dict) -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)


async def on_shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [poll_device]
    cron_jobs = [
        cron(
            enqueue_device_polls,
            second={0},  # ogni minuto al secondo 0 (cadenza base; raffinabile)
        )
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(get_settings().redis_url)
```
NOTE on ARQ API: verify the installed `arq` version's API for `RedisSettings`, `cron`, `enqueue_job`, and `WorkerSettings.redis_settings`/`redis_settings` attribute. If `redis_settings` must be a class attribute (not a method) or the `cron` signature differs, ADAPT to the installed version and REPORT. The contract the test pins: `WorkerSettings.functions` includes `poll_device`, `WorkerSettings.cron_jobs` is non-empty, and `poll_device`/`enqueue_device_polls` are callables. The enqueue cron interval should match `POLL_INTERVAL_SECONDS` conceptually (MVP: once per minute).

- [ ] **Step 3: Add the `worker` service to `backend/docker-compose.yml`** (for production; not used by tests):
```yaml
  worker:
    build: .
    command: ["arq", "app.worker.WorkerSettings"]
    environment:
      DATABASE_URL: postgresql+asyncpg://opngms_app:opngms_app@db:5432/opngms
      ADMIN_DATABASE_URL: postgresql+asyncpg://opngms:opngms@db:5432/opngms
      REDIS_URL: redis://redis:6379
      SESSION_SECRET: change-me
      MASTER_KEY: change-me-fernet-key
    depends_on:
      db: { condition: service_healthy }
      redis: { condition: service_healthy }
```
NOTE: this assumes a `Dockerfile` for the backend. If none exists, the `worker` service `build: .` will fail to build — in that case either add a minimal `backend/Dockerfile` (python:3.12-slim, copy, pip install -e ., default command) OR mark the worker service as documentation-only for now and run the worker locally with `cd backend && REDIS_URL=... ADMIN_DATABASE_URL=... .venv/bin/arq app.worker.WorkerSettings`. REPORT which you did; the tests do NOT require the compose worker service to run.

- [ ] **Step 4: Run + full suite + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
.venv/bin/python -m pytest tests/test_worker_config.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/app/worker.py backend/tests/test_worker_config.py backend/docker-compose.yml backend/Dockerfile 2>/dev/null
git commit -m "feat(backend): worker ARQ (poll_device + cron enqueue) + servizio worker compose"
```
Expected: worker config test passes; full suite green.

---

## Task 6: Smoke end-to-end del poller (fake client → metriche in TimescaleDB)

**Files:** Create `backend/tests/test_poller_e2e.py`

- [ ] **Step 1: Test** — `backend/tests/test_poller_e2e.py`:
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.metric import Metric
from app.services.monitoring import collect_and_store


class FakeClient:
    async def get_system_info(self):
        return {"cpu_pct": 5.0, "mem_pct": 30.0, "disk_pct": 10.0, "uptime_seconds": 100}

    async def get_firmware_status(self):
        return {"product_version": "24.7"}


async def test_two_polls_produce_two_time_buckets(db_engine):
    from app.models.device import Device

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                 "VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"),
            {"i": did, "t": tid},
        )
        await s.commit()
    # due cicli con timestamp diversi
    for offset in (0, 1):
        async with factory() as s:
            device = await s.get(Device, did)
            now = datetime(2026, 6, 9, 12, offset, 0, tzinfo=timezone.utc)
            await collect_and_store(s, device, FakeClient(), now=now)
            await s.commit()
    async with factory() as s:
        cpu_points = (
            await s.execute(
                select(func.count()).select_from(Metric).where(Metric.device_id == did, Metric.metric == "cpu.pct")
            )
        ).scalar_one()
        assert cpu_points == 2  # due campionamenti distinti nella hypertable
```

- [ ] **Step 2: Run whole suite + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/backend
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest tests/test_poller_e2e.py -v
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q
git add backend/tests/test_poller_e2e.py
git commit -m "test(backend): smoke e2e poller (due cicli → due punti nella hypertable)"
```
Expected: full suite green; the hypertable accumulates time-series points across polls.

---

## Self-review (mappatura spec → task)
- **Spec §3-4 (infra+storage)** → Task 1 (TimescaleDB+Redis), Task 3 (hypertable metrics + retention).
- **Spec §5 (poller ARQ)** → Task 5 (worker, cron enqueue, poll_device), Task 4 (collect_and_store).
- **Spec §6 (connector)** → Task 2 (get_system_info; rete=2B).
- **Definizione di fatto 2A** (un device pollato → metriche nell'hypertable + stato aggiornato) →
  Task 4 + Task 6.
- **Rimandato per design:** RLS su metrics + isolamento read (2C); metriche di rete + alerting (2B);
  API + dashboard (2C/2D).

**Note di scope / debito:**
- RLS sull'hypertable `metrics` arriva in 2C (col read-path); in 2A il poller scrive come owner.
- Endpoint OPNsense `get_system_info` (`diagnostics/system/systemInformation`) e mapping campi DA
  VERIFICARE; mockati con respx.
- Worker compose service dipende da un `Dockerfile` backend — se assente, worker eseguibile in
  locale via `arq app.worker.WorkerSettings` (vedi Task 5 Step 3).
- `alembic check` non sarà pulito su `metrics` (create_hypertable/retention non sono DDL-modello);
  verifica a runtime che la hypertable esista (Task 3 Step 5).

**Placeholder scan:** ogni step ha codice/comando concreto. Le incertezze (endpoint OPNsense, API ARQ
esatta) sono esplicite e isolate dietro contratti pinnati dai test.
**Type consistency:** `collect_and_store(session, device, client, now)`, `Metric(time, device_id,
tenant_id, metric, label, value)`, `OpnsenseClient.get_system_info()`, `WorkerSettings.{functions,
cron_jobs}`, `poll_device(ctx, device_id)` coerenti tra i Task 2-6.

# OPNGMS — Fase 3 / Milestone 3A: Storage + Framework Ingest + Suricata — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest incrementale e idempotente degli alert Suricata (IDS/IPS) dalla flotta OPNsense in una hypertable `events` isolata per tenant, con cursore per-device e deduplica.

**Architecture:** Estende il worker ARQ con un secondo cron (`enqueue_event_ingests`, ~5 min) che accoda `ingest_device_events(device_id)`. Il job legge un cursore per `(device, source)`, interroga l'API OPNsense via `OpnsenseClient` (SSRF-guarded), normalizza gli alert IDS e li inserisce in `events` con `ON CONFLICT DO NOTHING` (dedup), avanzando il cursore. Owner DB (bypassa RLS) per le scritture; la RLS proteggerà `events` per le letture API (3C).

**Tech Stack:** Python 3.12+, FastAPI/SQLAlchemy 2.0 async, TimescaleDB (hypertable `events`), ARQ + Redis, Alembic, pytest + respx.

---

## Contesto per l'implementatore (leggere prima di iniziare)

Codebase backend in `/home/l0rdg3x/coding/OPNGMS/backend`. **Segui i pattern di Fase 2.**

- **Modello hypertable**: `app/models/metric.py` — `Metric` con PK composita che INCLUDE `time` (richiesto da Timescale), `__table_args__` con un `Index`. Replica per `events`.
- **Migrazione hypertable**: `migrations/versions/0005_timescale_metrics.py` — `create_table` + `create_hypertable('metrics','time')` + index + `add_retention_policy`. Replica per `events`.
- **Migrazione RLS**: `migrations/versions/0007_rls_metrics_alerts.py` — enable/force/policy + grant a `opngms_app` (con `GRANT SELECT ON <hypertable>` esplicito per la propagazione ai chunk Timescale). Replica per `events`.
- **RLS — fonte unica**: `app/core/rls.py` — `TENANT_TABLES` (oggi `["devices","metrics","alerts"]`). Le migrazioni storiche 0002/0003 sono PINNATE a `["devices"]` e 0007 a `["metrics","alerts"]`: aggiungendo `"events"` a `TENANT_TABLES` NON si rompe nulla, e la conftest dei test abilita la RLS su tutte le `TENANT_TABLES`.
- **conftest**: `tests/conftest.py` — la fixture `db_engine` crea l'estensione, fa `create_all`, `create_hypertable('metrics', ...)`, abilita la RLS (`enable_rls_statements()`), crea il ruolo `opngms_app` + grant. **Va aggiunto `create_hypertable('events', ...)`** (Task 1). Fixture utili: `two_tenants` (due tenant + un device ciascuno: `fw-a`/`fw-b`).
- **Worker**: `app/worker.py` — `enqueue_device_polls` (cron) + `poll_device` (job) + `WorkerSettings`. Replica il pattern per gli eventi.
- **Servizio di raccolta**: `app/services/monitoring.py` — `collect_and_store(session, device, client, now)`: try/except `OpnsenseError` resiliente, costruisce righe ORM, `session.add_all`, `flush`. Replica lo spirito per `ingest`.
- **Connettore**: `app/connectors/opnsense/client.py` — `OpnsenseClient`, metodo privato `_get(path)` (unico confine HTTP, SSRF-guarded, normalizzazione errori → `OpnsenseError` e sottoclassi). I metodi pubblici (`get_interfaces`, ecc.) ritornano dict normalizzati. Replica per `get_ids_alerts`.
- **Test connettore**: `tests/test_connector_network.py` / `test_connector_system_info.py` — usano `respx` per mockare le risposte HTTP.

**Comando test** (dir `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
DB di test in Docker (`docker compose ps` → `db`). Suite attuale: **127 test verdi**.

**`alembic check` su DB pulito** (procedura usata in Fase 2): crea DB `opngms_check` + extension timescaledb, `alembic upgrade head`, `alembic check` (atteso "No new upgrade operations detected"), drop. Le env `SESSION_SECRET`/`MASTER_KEY` sono richieste (vedi i piani 2C/2D).

⚠️ **Endpoint OPNsense IDS DA VERIFICARE**: il vero endpoint (presumibilmente `ids/service/queryAlerts`) e il formato del payload non sono confermati. Il connettore `get_ids_alerts` è scritto contro un payload *plausibile* e testato con respx; il mapping si conferma su un device reale. **NON** è un blocco: l'astrazione e i test reggono comunque.

---

## File Structure

| File | Responsabilità | Azione |
|------|----------------|--------|
| `app/models/event.py` | `Event` (hypertable) | Create |
| `app/models/ingest_cursor.py` | `IngestCursor` (stato worker) | Create |
| `app/models/__init__.py` | Esporta i nuovi modelli | Modify |
| `app/core/rls.py` | `"events"` in `TENANT_TABLES` | Modify |
| `migrations/versions/0008_events_ingest.py` | events hypertable + ingest_cursors + RLS + grant | Create |
| `tests/conftest.py` | `create_hypertable('events', ...)` | Modify |
| `app/connectors/opnsense/client.py` | `get_ids_alerts(since)` | Modify |
| `app/services/ingest.py` | `ingest_events(...)` (cursore, dedup, IDS) | Create |
| `app/worker.py` | cron `enqueue_event_ingests` + `ingest_device_events` | Modify |
| `tests/test_event_model.py`, `tests/test_rls_isolation.py` | modello + isolamento RLS events | Create/Modify |
| `tests/test_connector_ids.py` | respx per `get_ids_alerts` | Create |
| `tests/test_ingest.py` | scrittura/cursore/idempotenza/resilienza | Create |
| `tests/test_worker_config.py` | wiring cron/job | Modify |

---

## Task 1: Modelli `events` + `ingest_cursors`, migrazione 0008, RLS

**Files:**
- Create: `app/models/event.py`, `app/models/ingest_cursor.py`
- Modify: `app/models/__init__.py`, `app/core/rls.py`, `tests/conftest.py`
- Create: `migrations/versions/0008_events_ingest.py`
- Create: `tests/test_event_model.py`; Modify: `tests/test_rls_isolation.py`

- [ ] **Step 1: Scrivere il modello `Event`**

Crea `app/models/event.py` (mirror di `metric.py`; PK composita = chiave di dedup, include `time`):
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index(
            "ix_events_tenant_device_source_time",
            "tenant_id", "device_id", "source", "time",
        ),
    )

    # PK composita che include `time` (richiesto da Timescale) ed è anche la chiave
    # di deduplica: stesso (time, device, source, event_key) -> stesso evento.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(String, primary_key=True)         # 'ids' | 'dns'
    event_key: Mapped[str] = mapped_column(String, primary_key=True)      # id sorgente o hash contenuto
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    category: Mapped[str] = mapped_column(String, default="", server_default="")
    src_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    dst_ip: Mapped[str] = mapped_column(String, default="", server_default="")
    name: Mapped[str] = mapped_column(String, default="", server_default="")
    severity: Mapped[str] = mapped_column(String, default="", server_default="")
    action: Mapped[str] = mapped_column(String, default="", server_default="")
    attributes: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
```

- [ ] **Step 2: Scrivere il modello `IngestCursor`**

Crea `app/models/ingest_cursor.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class IngestCursor(Base):
    """Watermark per-(device, source) dell'ingest. Stato interno del worker, NON user-facing
    (niente RLS): mai esposto via API."""

    __tablename__ = "ingest_cursors"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    source: Mapped[str] = mapped_column(String, primary_key=True)
    last_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    last_ref: Mapped[str | None] = mapped_column(String, default=None)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 3: Esportare i modelli**

In `app/models/__init__.py`, aggiungi gli import dei nuovi modelli accanto agli esistenti (così `Base.metadata` li include per `create_all`/autogenerate). Segui lo stile del file (es. `from app.models.event import Event` e `from app.models.ingest_cursor import IngestCursor`, e aggiungili a `__all__` se presente).

- [ ] **Step 4: Aggiungere `events` alla RLS**

In `app/core/rls.py`, riga `TENANT_TABLES`:
```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events"]
```
(`ingest_cursors` NON va aggiunta: è stato interno del worker, non esposto via API.)

- [ ] **Step 5: Aggiornare la conftest (hypertable events)**

In `tests/conftest.py`, nella fixture `db_engine`, subito dopo la riga
`await conn.execute(text("SELECT create_hypertable('metrics', 'time', if_not_exists => true)"))`
aggiungi:
```python
await conn.execute(text("SELECT create_hypertable('events', 'time', if_not_exists => true)"))
```
(L'ordine: `create_all` → create_hypertable metrics → create_hypertable events → `enable_rls_statements()` → ruolo+grant. `enable_rls_statements()` ora copre anche `events`.)

- [ ] **Step 6: Scrivere la migrazione 0008**

Crea `migrations/versions/0008_events_ingest.py`:
```python
"""events hypertable + ingest_cursors + RLS su events"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # events (hypertable)
    op.create_table(
        "events",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event_key", sa.String(), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default=""),
        sa.Column("src_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("dst_ip", sa.String(), nullable=False, server_default=""),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("severity", sa.String(), nullable=False, server_default=""),
        sa.Column("action", sa.String(), nullable=False, server_default=""),
        sa.Column("attributes", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint("time", "device_id", "source", "event_key"),
    )
    op.execute("SELECT create_hypertable('events', 'time')")
    op.create_index(
        "ix_events_tenant_device_source_time",
        "events",
        ["tenant_id", "device_id", "source", "time"],
    )
    op.execute("SELECT add_retention_policy('events', INTERVAL '90 days')")

    # ingest_cursors (stato worker, no RLS)
    op.create_table(
        "ingest_cursors",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("last_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ref", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "source"),
    )

    # RLS su events + grant a opngms_app (con propagazione ai chunk Timescale)
    op.execute("ALTER TABLE events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("events"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)
    op.execute(f"GRANT SELECT ON events TO {APP_ROLE}")  # propaga ai chunk dell'hypertable
    # ingest_cursors non è user-facing: nessuna RLS.


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON events FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON events")
    op.execute("ALTER TABLE events NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE events DISABLE ROW LEVEL SECURITY")
    op.drop_table("ingest_cursors")
    op.execute("SELECT remove_retention_policy('events', if_exists => true)")
    op.drop_table("events")
```

- [ ] **Step 7: Scrivere il test del modello + isolamento RLS**

Crea `tests/test_event_model.py` (insert + lettura come owner):
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context


async def test_event_insert_and_dedup(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    did = uuid.uuid4()
    async with factory() as s:  # owner -> bypassa RLS
        for _ in range(2):  # due insert identici -> dedup via PK
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN', '10.0.0.5') "
                    "ON CONFLICT DO NOTHING"
                ),
                {"t": now, "d": did, "tid": tenant_a},
            )
        await s.commit()
        n = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert n == 1  # il secondo insert è stato deduplicato
```

In `tests/test_rls_isolation.py`, estendi `test_rls_statements_cover_metrics_and_alerts` (o aggiungi un test) per includere `events`:
```python
def test_rls_statements_cover_events():
    assert "events" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    assert "ALTER TABLE events ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE events FORCE ROW LEVEL SECURITY" in sql
```
E un test di isolamento raw (mirror di `test_metrics_alerts_isolated_cross_tenant`):
```python
async def test_events_isolated_cross_tenant(db_engine, two_tenants):
    import os
    import uuid as _uuid
    from datetime import datetime, timezone

    tenant_a, tenant_b = two_tenants
    owner = async_sessionmaker(db_engine, expire_on_commit=False)
    async with owner() as s:  # owner bypassa RLS, inserisce per entrambi
        for tid, key in ((tenant_a, "a"), (tenant_b, "b")):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name) "
                    "VALUES (:t, :d, 'ids', :k, :tid, 'sig')"
                ),
                {"t": datetime.now(timezone.utc), "d": _uuid.uuid4(), "k": key, "tid": tid},
            )
        await s.commit()

    base_url = make_url(os.environ["TEST_DATABASE_URL"])
    app_url = base_url.set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            await set_tenant_context(s, tenant_a)
            keys = (await s.execute(text("SELECT event_key FROM events"))).scalars().all()
            assert keys == ["a"]  # solo il tenant A; la RLS esclude B (query raw senza filtro tenant)
        async with factory() as s2:
            assert (await s2.execute(text("SELECT event_key FROM events"))).scalars().all() == []
    finally:
        await engine.dispose()
```
(`make_url`/`make_engine`/`APP_ROLE`/`APP_ROLE_PASSWORD`/`set_tenant_context` sono già importati nel file.)

- [ ] **Step 8: Eseguire i test + alembic check**

Run: `... pytest tests/test_event_model.py tests/test_rls_isolation.py -v` → tutti PASS.
Run: l'INTERA suite `... pytest -q` → verde (127 + i nuovi).
Run: la procedura `alembic check` su DB pulito (upgrade head → check) → "No new upgrade operations detected." Verifica anche il round-trip downgrade/upgrade della 0008.

- [ ] **Step 9: Commit**
```bash
git add app/models/event.py app/models/ingest_cursor.py app/models/__init__.py app/core/rls.py \
        migrations/versions/0008_events_ingest.py tests/conftest.py tests/test_event_model.py tests/test_rls_isolation.py
git commit -m "feat(backend): hypertable events + ingest_cursors + RLS (migrazione 0008)"
```

---

## Task 2: Connettore `get_ids_alerts`

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_ids.py`

- [ ] **Step 1: Scrivere il test respx (fallisce)**

Crea `tests/test_connector_ids.py`. Mocka una risposta IDS *plausibile* (lista di righe alert) e verifica la normalizzazione:
```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_ids_alerts_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "src_ip": "10.0.0.5", "dest_ip": "1.2.3.4",
                "alert": {"signature": "ET SCAN Nmap", "severity": 2, "action": "allowed"},
                "alert_id": "abc123",
            }
        ]
    }
    respx.get(url__regex=r".*/api/ids/service/queryAlerts.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_ids_alerts(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.5"
    assert e["dst_ip"] == "1.2.3.4"
    assert e["name"] == "ET SCAN Nmap"
    assert e["severity"] == "2"
    assert e["action"] == "allowed"
    assert e["category"] == "alert"
    assert e["event_key"]  # presente (id sorgente o hash)
    assert e["time"].tzinfo is not None  # datetime tz-aware
```
(Il file usa lo stile di `tests/test_connector_network.py`; se serve, importa `pytest` e marca async come gli altri test.)

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `... pytest tests/test_connector_ids.py -v` → FAIL (`get_ids_alerts` inesistente).

- [ ] **Step 3: Implementare `get_ids_alerts`**

In `app/connectors/opnsense/client.py`, aggiungi (dopo `get_vpn_status`). Importa `datetime` e `hashlib` in cima al file se non presenti.
```python
    async def get_ids_alerts(self, since: "datetime | None" = None) -> list[dict]:
        """Alert Suricata IDS/IPS normalizzati.

        NOTA: endpoint `ids/service/queryAlerts` e formato del payload DA VERIFICARE
        su un OPNsense reale. Difensivo verso varianti di chiave. `since` è un hint:
        il filtro fine e la deduplica avvengono a valle (cursore + ON CONFLICT).
        """
        data = await self._get("ids/service/queryAlerts")
        out: list[dict] = []
        for r in data.get("rows", data.get("alerts", [])):
            alert = r.get("alert", {}) if isinstance(r.get("alert"), dict) else {}
            ts = self._parse_ts(r.get("timestamp"))
            name = alert.get("signature") or r.get("signature") or ""
            src = r.get("src_ip", "")
            dst = r.get("dest_ip", r.get("dst_ip", ""))
            action = alert.get("action", r.get("action", ""))
            severity = str(alert.get("severity", r.get("severity", "")))
            key = r.get("alert_id") or r.get("_id") or self._event_key(ts, src, dst, name, severity)
            out.append({
                "time": ts,
                "category": "alert",
                "src_ip": src,
                "dst_ip": dst,
                "name": name,
                "severity": severity,
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out

    @staticmethod
    def _parse_ts(value) -> "datetime":
        from datetime import datetime, timezone

        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _event_key(ts, *parts) -> str:
        import hashlib

        h = hashlib.sha1("|".join([ts.isoformat(), *[str(p) for p in parts]]).encode())
        return h.hexdigest()
```

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `... pytest tests/test_connector_ids.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_ids.py
git commit -m "feat(backend): connettore get_ids_alerts (normalizzazione alert Suricata)"
```

---

## Task 3: Servizio di ingest (cursore, dedup, IDS)

**Files:**
- Create: `app/services/ingest.py`
- Create: `tests/test_ingest.py`

- [ ] **Step 1: Scrivere i test (falliscono)**

Crea `tests/test_ingest.py`. Usa un client fake iniettato (no HTTP). Verifica: scrittura eventi, avanzamento cursore, **idempotenza** (re-run non duplica), **resilienza** (errore source non solleva).
```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, alerts, fail=False):
        self._alerts = alerts
        self._fail = fail

    async def get_ids_alerts(self, since=None):
        if self._fail:
            raise ReachabilityError("boom")
        return self._alerts


def _alert(ts, key, src="10.0.0.5", name="ET SCAN"):
    return {
        "time": ts, "category": "alert", "src_ip": src, "dst_ip": "1.2.3.4",
        "name": name, "severity": "2", "action": "allowed", "event_key": key, "attributes": {},
    }


async def _device(db_engine, tenant_id) -> Device:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_ingest_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        client = FakeClient([_alert(now, "k1"), _alert(now, "k2")])
        n = await ingest_events(s, device, client, now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='ids'"),
            {"d": did},
        )).scalar_one()
    assert cnt == 2
    assert cur == now


async def test_ingest_idempotent(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for _ in range(2):  # due run con gli stessi eventi
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient([_alert(now, "k1")]), now)
            await s.commit()
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert cnt == 1  # nessun duplicato


async def test_ingest_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient([], fail=True), now)  # source solleva
        await s.commit()
    assert n == 0  # nessun crash, zero eventi
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `... pytest tests/test_ingest.py -v` → FAIL (`app.services.ingest` inesistente).

- [ ] **Step 3: Implementare il servizio**

Crea `app/services/ingest.py`:
```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.event import Event
from app.models.ingest_cursor import IngestCursor

# Sorgenti attive: la 3B aggiungerà "dns".
SOURCES = ["ids"]


async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingerisce gli eventi (per source) di un device. Ritorna il n. di eventi nuovi visti.

    Resiliente: l'errore di una source non blocca le altre né solleva. Idempotente:
    cursore per (device, source) + insert ON CONFLICT DO NOTHING sulla PK di dedup.
    """
    total = 0
    for source in SOURCES:
        try:
            total += await _ingest_source(session, device, client, source)
        except OpnsenseError:
            continue  # una source non disponibile non blocca le altre
    return total


async def _ingest_source(session: AsyncSession, device: Device, client, source: str) -> int:
    cursor = await session.get(IngestCursor, (device.id, source))
    since = cursor.last_time if cursor else None
    raw = await _fetch(client, source, since)
    rows = [_normalize(device, source, r) for r in raw]
    if since is not None:
        rows = [r for r in rows if r["time"] > since]  # best-effort client-side
    if not rows:
        return 0
    await session.execute(pg_insert(Event).values(rows).on_conflict_do_nothing())
    new_max = max(r["time"] for r in rows)
    await _advance_cursor(session, device.id, source, new_max)
    return len(rows)


async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    raise ValueError(f"source sconosciuta: {source}")


def _normalize(device: Device, source: str, r: dict) -> dict:
    return {
        "time": r["time"],
        "device_id": device.id,
        "tenant_id": device.tenant_id,
        "source": source,
        "category": r.get("category", ""),
        "src_ip": r.get("src_ip", ""),
        "dst_ip": r.get("dst_ip", ""),
        "name": r.get("name", ""),
        "severity": r.get("severity", ""),
        "action": r.get("action", ""),
        "event_key": r["event_key"],
        "attributes": r.get("attributes", {}),
    }


async def _advance_cursor(session: AsyncSession, device_id, source: str, new_time: datetime) -> None:
    stmt = (
        pg_insert(IngestCursor)
        .values(device_id=device_id, source=source, last_time=new_time)
        .on_conflict_do_update(
            index_elements=["device_id", "source"],
            set_={"last_time": new_time},
        )
    )
    await session.execute(stmt)
```

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `... pytest tests/test_ingest.py -v` → PASS (3/3). Poi l'INTERA suite verde.

- [ ] **Step 5: Commit**
```bash
git add app/services/ingest.py tests/test_ingest.py
git commit -m "feat(backend): servizio ingest_events (cursore + dedup ON CONFLICT, source IDS)"
```

---

## Task 4: Wiring nel worker (cron + job)

**Files:**
- Modify: `app/worker.py`
- Modify: `tests/test_worker_config.py`

- [ ] **Step 1: Scrivere/estendere il test di wiring (fallisce)**

In `tests/test_worker_config.py`, aggiungi un test che verifica che il worker esponga la funzione e il cron dell'ingest. Adatta allo stile del file esistente (che già testa `WorkerSettings`):
```python
def test_worker_exposes_event_ingest():
    from app.worker import WorkerSettings, ingest_device_events

    assert ingest_device_events in WorkerSettings.functions
    # due cron: poll metriche + ingest eventi
    assert len(WorkerSettings.cron_jobs) >= 2
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `... pytest tests/test_worker_config.py -v` → FAIL (`ingest_device_events` inesistente).

- [ ] **Step 3: Implementare il wiring**

In `app/worker.py`:
- import: `from app.services.ingest import ingest_events`.
- aggiungi le due funzioni (mirror di `enqueue_device_polls`/`poll_device`):
```python
async def enqueue_event_ingests(ctx: dict) -> int:
    """Cron: accoda un ingest_device_events per ogni device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("ingest_device_events", str(device_id))
    return len(ids)


async def ingest_device_events(ctx: dict, device_id: str) -> int:
    """Job: ingerisce gli eventi (IDS) di un singolo device."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return 0
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        n = await ingest_events(session, device, client, now=datetime.now(timezone.utc))
        await session.commit()
        return n
```
- aggiorna `WorkerSettings`:
```python
class WorkerSettings:
    functions = [poll_device, ingest_device_events]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),               # metriche, ogni minuto
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),  # eventi, ogni 5 minuti
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
```

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `... pytest tests/test_worker_config.py -v` → PASS. Poi l'INTERA suite verde.

- [ ] **Step 5: Commit**
```bash
git add app/worker.py tests/test_worker_config.py
git commit -m "feat(backend): worker — cron enqueue_event_ingests + job ingest_device_events"
```

---

## Task 5: Debito tecnico

- [ ] **Step 1: Registrare il debito 3A**

Append a questo piano:
```markdown
## Debito tecnico (3A)

- **Endpoint OPNsense IDS DA VERIFICARE**: `ids/service/queryAlerts` e il formato del payload sono
  plausibili ma non confermati su un device reale. Il connettore è difensivo verso varianti di chiave;
  da rifinire col device reale (e probabilmente paginazione/filtro server-side per `since`).
- **`since` solo client-side**: l'ingest filtra `time > last_time` lato client dopo il fetch; senza
  filtro/paginazione server-side si rifetcha la finestra recente ad ogni run (la dedup evita duplicati
  ma c'è rilavoro). Aggiungere il filtro server-side quando l'endpoint reale è noto.
- **Niente overlap δ sul cursore**: eventi in arrivo tardivo con `time <= last_time` non visti prima
  verrebbero saltati. Accettabile per report periodici; valutare un piccolo overlap + dedup.
- **Cadenza ingest fissa (5 min)**: il cron usa un set di minuti fisso; rendere
  `INGEST_INTERVAL_SECONDS` configurabile (oggi hardcoded).
- **Compressione hypertable assente**: solo retention 90g. Aggiungere compression policy Timescale per
  il volume eventi.
- **`event_key` hash del contenuto** quando la sorgente non dà un id stabile: due eventi identici allo
  stesso istante collassano in uno (accettabile). Preferire l'id sorgente quando disponibile.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase3-milestone3A-ingest-suricata.md
git commit -m "docs: debito tecnico milestone 3A"
```

---

## Definizione di "fatto" (3A)
- L'hypertable `events` esiste, isolata per tenant dalla RLS (test cross-tenant raw), con dedup via PK.
- Il connettore `get_ids_alerts` normalizza gli alert Suricata (respx).
- `ingest_events` scrive gli eventi IDS, avanza il cursore, è idempotente e resiliente agli errori di source.
- Il worker espone il cron `enqueue_event_ingests` + il job `ingest_device_events`.
- Suite verde + `alembic check` pulito.

---

## Debito tecnico (3A) — consolidato dalle review

- **Endpoint OPNsense IDS DA VERIFICARE**: `ids/service/queryAlerts` e il formato del payload sono
  plausibili ma non confermati su un device reale. Il connettore è difensivo verso varianti di chiave;
  da rifinire col device reale (probabilmente POST/paginazione/filtro server-side per `since`).
- **`since` solo client-side**: l'ingest filtra `time > last_time` lato client dopo il fetch; senza
  filtro/paginazione server-side si rifetcha la finestra recente ad ogni run (la dedup evita duplicati
  ma c'è rilavoro). Aggiungere il filtro server-side quando l'endpoint reale è noto. (`since` è
  accettato dal connettore ma ignorato — review Task 2.)
- **Niente overlap δ sul cursore**: eventi in arrivo tardivo con `time <= last_time` non visti prima
  verrebbero saltati. Accettabile per report periodici; valutare un piccolo overlap + dedup.
- **Cadenza ingest fissa (5 min)**: il cron usa un set di minuti fisso; rendere
  `INGEST_INTERVAL_SECONDS` configurabile (oggi hardcoded).
- **Compressione hypertable assente**: solo retention 90g. Aggiungere compression policy Timescale per
  il volume eventi.
- **`event_key` hash del contenuto** quando la sorgente non dà un id stabile: due eventi *davvero*
  identici allo stesso istante collassano in uno (accettabile, dedup voluta). Preferire l'id sorgente
  quando disponibile (già fatto: `alert_id`/`_id` → fallback hash).
- **`_normalize` accede a `r["time"]`/`r["event_key"]` con indicizzazione hard** (review Task 3): un
  cambio di contratto del connettore darebbe KeyError. Accettabile (fail-fast su payload malformato).
- **`now` non usato in `ingest_events`**: mantenuto in firma per omogeneità col poller; valutare se
  usarlo per il watermark o rimuoverlo.

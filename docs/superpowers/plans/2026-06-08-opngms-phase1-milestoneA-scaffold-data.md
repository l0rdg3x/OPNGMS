# OPNGMS Fase 1 · Milestone A — Scaffold & Fondamenta Dati — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avere un backend OPNGMS che compila e parte, con lo schema dati multi-tenant su Postgres, le migrazioni Alembic, e l'isolamento cross-tenant garantito da Row-Level Security e dimostrato dai test.

**Architecture:** Backend Python/FastAPI async a strati (`api` → `services` → `repositories` → `models`). Postgres con schema condiviso + colonna `tenant_id` sulle tabelle di dati-tenant; isolamento a doppio livello: filtro applicativo nei repository **e** policy RLS Postgres pilotate dalla variabile di sessione `app.current_tenant`. Le tabelle di "control plane" (users, tenants, memberships, audit, sessions) restano fuori dalla RLS-tenant e verranno scoperte a livello service nelle milestone successive.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async) + asyncpg, Alembic, pydantic v2 + pydantic-settings, Postgres 16, pytest + pytest-asyncio + httpx.

---

## Riferimento spec

Questo piano implementa le sezioni 5, 6, 7 e 14 dello spec
`docs/superpowers/specs/2026-06-08-opngms-foundation-inventory-design.md`
(architettura, modello dati, multi-tenancy & isolamento, struttura backend). Auth/RBAC/audit
(sez. 8-10), onboarding/segreti/connector (sez. 11-13) e frontend (sez. 15) sono nelle
Milestone B, C, D.

## Struttura file (creata in questa milestone)

```
backend/
  pyproject.toml                 # dipendenze + config pytest
  .env.example                   # variabili d'ambiente di esempio
  docker-compose.yml             # Postgres per sviluppo/test
  alembic.ini                    # config Alembic
  Makefile                       # scorciatoie (up, test, migrate)
  app/
    __init__.py
    main.py                      # app FastAPI + /healthz
    core/
      __init__.py
      config.py                  # Settings (pydantic-settings)
      db.py                      # engine async, session factory, set_tenant_context
      rls.py                     # tabelle tenant + SQL RLS (DRY: usato da migrazione e test)
    models/
      __init__.py                # importa tutti i modelli per il metadata
      base.py                    # DeclarativeBase + mixin id/created_at
      tenant.py
      user.py
      membership.py
      device.py
      audit.py
      session.py
    repositories/
      __init__.py
      device.py                  # DeviceRepository (scoping applicativo per tenant)
  migrations/
    env.py                       # env Alembic (async, target Base.metadata)
    script.py.mako
    versions/
      0001_initial.py            # tutte le tabelle (autogenerate)
      0002_rls.py                # ENABLE/FORCE RLS + policy (hand-written)
  tests/
    __init__.py
    conftest.py                  # client HTTP, engine test, sessione con tenant context
    test_health.py
    test_config.py
    test_models.py
    test_rls_isolation.py        # il test critico di isolamento
```

---

## Task 1: Scaffold progetto + FastAPI parte + `/healthz`

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/app/__init__.py`, `backend/app/main.py`
- Create: `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_health.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_health.py`:
```python
async def test_healthz_ok(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

`backend/tests/conftest.py` (solo la fixture client per ora):
```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

`backend/pyproject.toml`:
```toml
[project]
name = "opngms-backend"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy[asyncio]>=2.0",
    "asyncpg>=0.29",
    "alembic>=1.13",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "argon2-cffi>=23.1",
    "cryptography>=42.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.setuptools.packages.find]
include = ["app*"]
```

Create empty `backend/app/__init__.py` and `backend/tests/__init__.py`.

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `python -m pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.main'` (main.py non esiste ancora).

- [ ] **Step 3: Write minimal implementation**

`backend/app/main.py`:
```python
from fastapi import FastAPI

app = FastAPI(title="OPNGMS", version="0.1.0")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
```

Install deps first: `pip install -e ".[dev]"` (from `backend/`).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_health.py -v`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pyproject.toml backend/app backend/tests
git commit -m "feat(backend): scaffold FastAPI app con endpoint /healthz"
```

---

## Task 2: Configurazione via pydantic-settings

**Files:**
- Create: `backend/app/core/__init__.py`, `backend/app/core/config.py`
- Create: `backend/tests/test_config.py`
- Create: `backend/.env.example`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_config.py`:
```python
from app.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/opngms")
    monkeypatch.setenv("SESSION_SECRET", "session-secret")
    monkeypatch.setenv("MASTER_KEY", "bWFzdGVyLWtleS0zMi1ieXRlcy1sb25nLXh4eHh4eHg=")
    settings = Settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.session_secret == "session-secret"
    assert settings.session_ttl_hours == 12  # default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.config'`.

- [ ] **Step 3: Write minimal implementation**

Create empty `backend/app/core/__init__.py`.

`backend/app/core/config.py`:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None
    session_secret: str
    master_key: str  # Fernet key urlsafe-base64 (usata dalla Milestone C)
    session_ttl_hours: int = 12


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`backend/.env.example`:
```bash
DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test
SESSION_SECRET=change-me-session-secret
# Genera con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MASTER_KEY=change-me-fernet-key
SESSION_TTL_HOURS=12
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/__init__.py backend/app/core/config.py backend/tests/test_config.py backend/.env.example
git commit -m "feat(backend): Settings via pydantic-settings + .env.example"
```

---

## Task 3: Postgres locale (docker-compose) + Makefile

**Files:**
- Create: `backend/docker-compose.yml`
- Create: `backend/Makefile`

Nota: questo task non ha test automatici — è infrastruttura. La verifica è manuale (avvio + connessione).

- [ ] **Step 1: Create docker-compose**

`backend/docker-compose.yml`:
```yaml
services:
  db:
    image: postgres:16
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

volumes:
  opngms_pg:
```

- [ ] **Step 2: Create Makefile**

`backend/Makefile`:
```makefile
.PHONY: up down test migrate revision createtestdb

up:
	docker compose up -d db

down:
	docker compose down

createtestdb:
	docker compose exec -T db psql -U opngms -d opngms -c "CREATE DATABASE opngms_test;" || true

migrate:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

test:
	python -m pytest -v
```

- [ ] **Step 3: Verify Postgres boots and is reachable**

Run:
```bash
cd backend && make up && make createtestdb
docker compose exec -T db psql -U opngms -d opngms -c "SELECT 1;"
```
Expected: output con `1` riga `?column? = 1`, e il database `opngms_test` creato.

- [ ] **Step 4: Commit**

```bash
git add backend/docker-compose.yml backend/Makefile
git commit -m "chore(backend): Postgres via docker-compose + Makefile"
```

---

## Task 4: Engine async + DeclarativeBase + helper tenant context

**Files:**
- Create: `backend/app/core/db.py`
- Create: `backend/app/models/__init__.py`, `backend/app/models/base.py`
- Test: copertura indiretta nei Task 5-8 (qui solo un test di import/connessione)
- Create: `backend/tests/test_db_connect.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_db_connect.py`:
```python
import os

import pytest
from sqlalchemy import text

from app.core.db import make_engine


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL non impostata"
)
async def test_engine_can_select_one():
    engine = make_engine(os.environ["TEST_DATABASE_URL"])
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
    await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test python -m pytest tests/test_db_connect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.db'`.

- [ ] **Step 3: Write minimal implementation**

`backend/app/core/db.py`:
```python
import uuid
from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = make_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _factory


async def set_tenant_context(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Imposta app.current_tenant per la transazione corrente (pilota la RLS)."""
    await session.execute(
        text("SELECT set_config('app.current_tenant', :tid, true)"),
        {"tid": str(tenant_id)},
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session
```

`backend/app/models/base.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDPKMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

Create empty `backend/app/models/__init__.py` (popolato nel Task 5).

- [ ] **Step 4: Run test to verify it passes**

Run: `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test python -m pytest tests/test_db_connect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/db.py backend/app/models/base.py backend/app/models/__init__.py backend/tests/test_db_connect.py
git commit -m "feat(backend): engine async, Base/mixin, helper set_tenant_context"
```

---

## Task 5: Modelli di dominio

**Files:**
- Create: `backend/app/models/tenant.py`, `user.py`, `membership.py`, `device.py`, `audit.py`, `session.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

`backend/tests/test_models.py`:
```python
from app.models import Base
from app.models.device import Device


def test_all_tables_registered():
    names = set(Base.metadata.tables.keys())
    assert {
        "tenants",
        "users",
        "memberships",
        "devices",
        "audit_log",
        "sessions",
    } <= names


def test_device_has_tenant_and_encrypted_secret_columns():
    cols = {c.name for c in Device.__table__.columns}
    assert "tenant_id" in cols
    assert "api_key_enc" in cols
    assert "api_secret_enc" in cols
    assert "status" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.device'`.

- [ ] **Step 3: Write minimal implementation**

`backend/app/models/tenant.py`:
```python
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Tenant(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str]
    slug: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(default="active")
    note: Mapped[str | None] = mapped_column(default=None)
```

`backend/app/models/user.py`:
```python
from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class User(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    password_hash: Mapped[str]
    is_superadmin: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(default="active")
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
```

`backend/app/models/membership.py`:
```python
import uuid

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Membership(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_membership_user_tenant"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    role: Mapped[str]  # tenant_admin | operator | read_only
```

`backend/app/models/device.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import ARRAY, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin


class Device(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "devices"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    base_url: Mapped[str]
    api_key_enc: Mapped[bytes] = mapped_column(LargeBinary)
    api_secret_enc: Mapped[bytes] = mapped_column(LargeBinary)
    verify_tls: Mapped[bool] = mapped_column(default=True)
    tls_fingerprint: Mapped[str | None] = mapped_column(default=None)
    site: Mapped[str | None] = mapped_column(default=None)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    status: Mapped[str] = mapped_column(default="unverified")  # reachable|unverified|unreachable
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    firmware_version: Mapped[str | None] = mapped_column(default=None)
```

`backend/app/models/audit.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class AuditLog(UUIDPKMixin, Base):
    __tablename__ = "audit_log"

    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), default=None
    )
    action: Mapped[str]
    target_type: Mapped[str | None] = mapped_column(default=None)
    target_id: Mapped[str | None] = mapped_column(default=None)
    ip: Mapped[str | None] = mapped_column(default=None)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
```

`backend/app/models/session.py`:
```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class Session(UUIDPKMixin, Base):
    __tablename__ = "sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
```

`backend/app/models/__init__.py`:
```python
from app.models.base import Base
from app.models.audit import AuditLog
from app.models.device import Device
from app.models.membership import Membership
from app.models.session import Session
from app.models.tenant import Tenant
from app.models.user import User

__all__ = ["Base", "AuditLog", "Device", "Membership", "Session", "Tenant", "User"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_models.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models
git commit -m "feat(backend): modelli Tenant/User/Membership/Device/AuditLog/Session"
```

---

## Task 6: Alembic + migrazione iniziale (0001)

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako`
- Create: `backend/migrations/versions/0001_initial.py` (via autogenerate)

- [ ] **Step 1: Inizializza Alembic e configura env async**

Run (da `backend/`): `alembic init -t async migrations`

Sovrascrivi `backend/migrations/env.py` con:
```python
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _db_url() -> str:
    import os

    return os.getenv("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(), target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_db_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
```

In `backend/alembic.ini`, lascia `sqlalchemy.url` vuoto (l'URL arriva da `env.py`):
```ini
sqlalchemy.url =
```

- [ ] **Step 2: Genera la migrazione iniziale (autogenerate)**

Run:
```bash
cd backend && make up
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
  alembic revision --autogenerate -m "initial schema"
```
Rinomina il file generato in `backend/migrations/versions/0001_initial.py` e imposta
`revision = "0001"`, `down_revision = None` in cima al file.

- [ ] **Step 3: Verifica upgrade e downgrade**

Run:
```bash
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms alembic upgrade head
docker compose exec -T db psql -U opngms -d opngms -c "\dt"
```
Expected: elenca le tabelle `tenants, users, memberships, devices, audit_log, sessions, alembic_version`.

```bash
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms alembic downgrade base
```
Expected: nessun errore (le tabelle vengono rimosse). Poi rifai `upgrade head`.

- [ ] **Step 4: Commit**

```bash
git add backend/alembic.ini backend/migrations
git commit -m "feat(backend): Alembic async + migrazione 0001 (schema iniziale)"
```

---

## Task 7: RLS — modulo SQL condiviso + migrazione 0002

**Files:**
- Create: `backend/app/core/rls.py`
- Create: `backend/migrations/versions/0002_rls.py`

Il modulo `rls.py` è la **singola fonte di verità** per gli statement RLS (DRY): lo usano
sia la migrazione sia la conftest dei test.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_rls_isolation.py` (solo la prima asserzione sugli statement; il test di
isolamento vero arriva al Task 9):
```python
from app.core.rls import TENANT_TABLES, enable_rls_statements


def test_rls_statements_cover_devices():
    assert "devices" in TENANT_TABLES
    sql = "\n".join(enable_rls_statements())
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "current_setting('app.current_tenant'" in sql
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_rls_isolation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.rls'`.

- [ ] **Step 3: Write minimal implementation**

`backend/app/core/rls.py`:
```python
"""Statement RLS per le tabelle di dati-tenant.

Fonte unica usata sia dalla migrazione 0002 sia dalla conftest dei test, così
policy applicate in produzione e in test non possono divergere.
"""

# Tabelle soggette a isolamento per tenant (le tabelle di control-plane NON sono qui).
TENANT_TABLES: list[str] = ["devices"]


def enable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE: la RLS si applica anche al proprietario della tabella (e quindi nei test).
        stmts.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        stmts.append(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid)"
        )
    return stmts


def disable_rls_statements() -> list[str]:
    stmts: list[str] = []
    for table in TENANT_TABLES:
        stmts.append(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        stmts.append(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        stmts.append(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    return stmts
```

`backend/migrations/versions/0002_rls.py`:
```python
from alembic import op

from app.core.rls import disable_rls_statements, enable_rls_statements

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for stmt in enable_rls_statements():
        op.execute(stmt)


def downgrade() -> None:
    for stmt in disable_rls_statements():
        op.execute(stmt)
```

- [ ] **Step 4: Run test + apply migration**

Run:
```bash
python -m pytest tests/test_rls_isolation.py::test_rls_statements_cover_devices -v
ALEMBIC_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms alembic upgrade head
docker compose exec -T db psql -U opngms -d opngms -c "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='devices';"
```
Expected: test PASS; la riga `devices` mostra `relrowsecurity = t` e `relforcerowsecurity = t`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/rls.py backend/migrations/versions/0002_rls.py backend/tests/test_rls_isolation.py
git commit -m "feat(backend): RLS su devices (modulo condiviso + migrazione 0002)"
```

---

## Task 8: Repository device con scoping applicativo

**Files:**
- Create: `backend/app/repositories/__init__.py`, `backend/app/repositories/device.py`
- Test: coperto dal Task 9 (isolamento end-to-end)

- [ ] **Step 1: Write the implementation**

Create empty `backend/app/repositories/__init__.py`.

`backend/app/repositories/device.py`:
```python
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device import Device


class DeviceRepository:
    """Accesso ai device già scoperto per tenant a livello applicativo.

    Doppio livello di isolamento: il filtro `tenant_id` qui + la RLS Postgres.
    """

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self) -> Sequence[Device]:
        result = await self.session.execute(
            select(Device).where(Device.tenant_id == self.tenant_id)
        )
        return result.scalars().all()

    async def add(self, device: Device) -> Device:
        device.tenant_id = self.tenant_id
        self.session.add(device)
        await self.session.flush()
        return device
```

- [ ] **Step 2: Verify it imports**

Run: `python -c "from app.repositories.device import DeviceRepository; print('ok')"`
Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add backend/app/repositories
git commit -m "feat(backend): DeviceRepository con scoping per tenant"
```

---

## Task 9: Test critico di isolamento cross-tenant (app + RLS)

**Files:**
- Modify: `backend/tests/conftest.py` (aggiungi fixture DB con migrazioni + sessione)
- Modify: `backend/tests/test_rls_isolation.py` (aggiungi i test di isolamento)

Questo è **il test che protegge l'invariante più importante dello spec**: un contesto-tenant
non può vedere i dati di un altro, nemmeno bypassando il filtro applicativo (lo garantisce la
RLS).

- [ ] **Step 1: Estendi conftest con DB di test**

Aggiungi a `backend/tests/conftest.py`:
```python
import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.rls import enable_rls_statements
from app.models import Base

TEST_DB_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB_URL, reason="TEST_DATABASE_URL non impostata")


@pytest.fixture(scope="session")
async def db_engine():
    engine = make_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        for stmt in enable_rls_statements():
            await conn.execute(text(stmt))
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        yield s
        await s.rollback()


@pytest.fixture
async def two_tenants(db_engine):
    """Crea due tenant + un device ciascuno (setup senza vincolo RLS sul tenant attivo)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    a, b = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'A','a','active')"),
            {"id": a},
        )
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'B','b','active')"),
            {"id": b},
        )
        # Inserimenti device: imposta il contesto al tenant giusto per superare WITH CHECK.
        await set_tenant_context(s, a)
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id,:t,'fw-a','https://a',''::bytea,''::bytea,true,'unverified','{}')"
            ),
            {"id": uuid.uuid4(), "t": a},
        )
        await set_tenant_context(s, b)
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id,:t,'fw-b','https://b',''::bytea,''::bytea,true,'unverified','{}')"
            ),
            {"id": uuid.uuid4(), "t": b},
        )
        await s.commit()
    return a, b
```

- [ ] **Step 2: Write the failing isolation tests**

Aggiungi a `backend/tests/test_rls_isolation.py`:
```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.device import DeviceRepository


async def test_repository_returns_only_active_tenant(db_engine, two_tenants):
    tenant_a, tenant_b = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_tenant_context(s, tenant_a)
        devices = await DeviceRepository(s, tenant_a).list()
        assert [d.name for d in devices] == ["fw-a"]


async def test_rls_blocks_cross_tenant_even_without_app_filter(db_engine, two_tenants):
    """Bypassa il filtro applicativo: SELECT grezza. La RLS deve comunque isolare."""
    tenant_a, tenant_b = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_tenant_context(s, tenant_a)
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == ["fw-a"]  # NON vede fw-b, pur senza WHERE tenant_id

        await set_tenant_context(s, tenant_b)
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == ["fw-b"]


async def test_no_tenant_context_sees_nothing(db_engine, two_tenants):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # nessun set_tenant_context → current_setting è NULL → nessuna riga
        rows = (await s.execute(text("SELECT name FROM devices"))).scalars().all()
        assert rows == []
```

- [ ] **Step 3: Run tests to verify they fail (then pass)**

Run: `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test python -m pytest tests/test_rls_isolation.py -v`

Sequenza attesa: se i modelli/RLS sono corretti dai task precedenti, questi test **passano**
direttamente perché esercitano codice già scritto. Se falliscono, la causa più probabile è la
RLS non applicata al DB di test → verifica che la fixture `db_engine` esegua
`enable_rls_statements()` dopo `create_all`. Expected finale: PASS (3 passed).

- [ ] **Step 4: Commit**

```bash
git add backend/tests/conftest.py backend/tests/test_rls_isolation.py
git commit -m "test(backend): isolamento cross-tenant provato via repository e RLS grezza"
```

---

## Task 10: Suite verde end-to-end + README

**Files:**
- Create: `backend/README.md`

- [ ] **Step 1: Run l'intera suite**

Run:
```bash
cd backend && make up && make createtestdb
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test python -m pytest -v
```
Expected: tutti i test PASS (health, config, db_connect, models, rls_isolation).

- [ ] **Step 2: Write README**

`backend/README.md`:
```markdown
# OPNGMS Backend

## Setup
1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -e ".[dev]"`
3. Copia `.env.example` in `.env` e genera `MASTER_KEY`:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. `make up && make createtestdb`
5. `make migrate` (applica le migrazioni al DB di sviluppo)

## Test
`TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test make test`

## Avvio
`uvicorn app.main:app --reload`  → http://localhost:8000/healthz
```

- [ ] **Step 3: Commit**

```bash
git add backend/README.md
git commit -m "docs(backend): README setup/test/avvio Milestone A"
```

---

## Self-review (mappatura spec → task)

- **Spec §5 Architettura** (app a strati, connector isolato) → struttura cartelle Task 1/4/5/8;
  il connector è Milestone C.
- **Spec §6 Modello dati** (Tenant, User, Membership, Device, AuditLog) → Task 5 (+ Session per
  le sessioni della Milestone B).
- **Spec §7 Multi-tenancy & isolamento** (app + RLS, `app.current_tenant`) → Task 4
  (`set_tenant_context`), Task 7 (RLS), Task 8 (filtro app), Task 9 (test di prova).
- **Spec §14 Struttura backend** (FastAPI, SQLAlchemy async, Alembic, pydantic-settings) →
  Task 1/2/4/6.
- **Fuori da questa milestone (per design):** auth/RBAC/audit (B), segreti/connector/onboarding
  (C), frontend (D). I campi `password_hash`, `api_key_enc`, `api_secret_enc`, `sessions` sono
  già nello schema per non rifare migrazioni a ogni milestone.

**Nota di refinement allo spec:** la RLS-tenant si applica alle tabelle di **dati-tenant**
(in Fase 1: `devices`; in futuro metriche/eventi/config). Le tabelle di **control-plane**
(`users`, `tenants`, `memberships`, `audit_log`, `sessions`) restano fuori dalla RLS-tenant e
vengono scoperte a livello service: evita il problema "uovo-gallina" di dover risolvere le
membership di un utente *prima* di conoscere il tenant attivo. Coerente con l'intento dello
spec (la RLS come rete di sicurezza sui dati dei clienti).

**Placeholder scan:** nessun TBD/TODO; ogni step ha codice o comando concreto.
**Type consistency:** `set_tenant_context(session, tenant_id)`, `DeviceRepository(session,
tenant_id)`, `enable_rls_statements()`, `TENANT_TABLES`, `Device.api_key_enc/api_secret_enc`,
`Device.status` usati in modo coerente tra Task 4-9.

---

## Task 11: Wiring RLS in produzione (ruolo app non-superuser) — aggiunto dopo il Task 9

**Motivazione (scoperta durante il Task 9):** i superuser PostgreSQL bypassano *sempre* la
RLS, anche con `FORCE`. L'utente `opngms` (POSTGRES_USER) è superuser, quindi finché l'app si
connette con lui la RLS non protegge in produzione. I test la esercitano solo via `SET ROLE`.
Per rendere la "rete di sicurezza" RLS realmente attiva, l'app deve connettersi con un ruolo
**non-superuser, NOBYPASSRLS**.

**Deliverable:**
- `app/core/db_roles.py` — fonte DRY: `APP_ROLE="opngms_app"`, `create_app_role_statements()`
  (`CREATE ROLE ... LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE`, guarded),
  `grant_app_role_statements()` (USAGE schema + CRUD su tutte le tabelle + ALTER DEFAULT
  PRIVILEGES per le tabelle future), `drop_app_role_statements()` (revoke + DROP OWNED BY +
  DROP ROLE, guarded).
- `app/core/rls.py` — rimosso il blocco `DO` idempotente (impediva l'update della policy su DB
  esistenti); fattorizzato `policy_create_statement(table)` (con `NULLIF(current_setting(...),'')`);
  `enable_rls_statements()` usa il `CREATE POLICY` semplice; aggiunto `recreate_policy_statements()`
  (DROP+CREATE) per aggiornare la policy sui DB già migrati.
- `migrations/versions/0003_app_role_and_policy.py` (`revision="0003"`, `down_revision="0002"`):
  upgrade = crea ruolo + ricrea policy (NULLIF) + grant; downgrade = drop ruolo/grant (la policy
  migliorata resta).
- `.env.example` — `DATABASE_URL` usa `opngms_app`; aggiunto `ADMIN_DATABASE_URL` (owner) per le
  migrazioni. `Makefile` `migrate` usa l'URL admin.
- `tests/conftest.py` — il ruolo + grant nel `db_engine` arrivano da `db_roles` (DRY); aggiunto
  un test che si connette *davvero* come `opngms_app` (non `SET ROLE`) e verifica l'isolamento.

**Definizione di fatto:** sul DB, `SELECT rolsuper, rolbypassrls FROM pg_roles WHERE
rolname='opngms_app'` → `f, f`; la policy `tenant_isolation` usa `NULLIF`; una connessione reale
come `opngms_app` vede solo i device del tenant in contesto; suite verde.

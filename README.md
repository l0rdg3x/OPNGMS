# OPNGMS — OPNsense Global Management System

A centralized, multi-tenant console for MSPs that manage and monitor a fleet of **OPNsense**
firewalls from a single pane of glass. Device inventory, health and network monitoring, alerting,
and — on the roadmap — log/event ingest, configuration push, and per-customer PDF reporting.

> **Status:** Phases 1 (Foundation & Inventory), 2 (Monitoring), and 3 (Log/Event ingest) are
> **complete**; Phase 4 (Config management) is **in progress** (4A — versioned config backup + drift —
> done). See [Roadmap & status](#roadmap--status).

---

## What it is

OPNGMS gives an MSP a single console to:

- **Inventory** customer OPNsense firewalls (onboarding, encrypted API secrets, reachability tests).
- **Monitor** the fleet: periodic polling via the OPNsense API → health metrics (CPU/mem/disk,
  uptime, firmware) and network metrics (interfaces, gateways, VPN), up/down status, alerts.
- **Ingest** security and browsing events (Suricata IDS/IPS alerts, DNS queries) to feed periodic
  reports.
- **Visualize** everything in a per-customer dashboard: fleet overview, per-device health with
  time-series charts, and an active/historical alert list.

Tenant isolation is **structural**: shared schema + `tenant_id` + Postgres **Row-Level Security**
(fail-closed), with the app running as a non-superuser role.

## Architecture

```
                ┌──────────────┐   cron        ┌──────────────┐
                │ ARQ scheduler ├──────────────►│ Redis (broker)│
                └──────────────┘  enqueue jobs  └──────┬───────┘
                  poll_device / ingest_device_events    │
                                                ┌──────▼────────┐  OpnsenseClient   ┌──────────┐
                                                │  ARQ worker(s) ├──────HTTPS───────►│ OPNsense │
                                                └──────┬────────┘  (SSRF-guarded)    │ sys, IDS │
                                                       │ metrics / status / alerts / events
   React + Mantine ──HTTP──► FastAPI ──RLS────► ┌──────▼─────────────────────────┐  (owner, bypass RLS)
   (dashboard)               (opngms_app)       │  metrics & events (hypertable), │
                                                │  alerts, devices, tenants, ...  │
                                                └─────────────────────────────────┘
```

- **API** (async FastAPI): session auth + CSRF, 4-role RBAC, tenant-scoped endpoints. Connects as the
  non-superuser `opngms_app` role → RLS filters every read per customer.
- **Worker** (ARQ + Redis): cron jobs enqueue per-device work. `poll_device` collects metrics via
  `OpnsenseClient` (the single HTTP boundary, with an SSRF guard) and writes them to a TimescaleDB
  hypertable, evaluating alerts; `ingest_device_events` pulls events (Suricata IDS) incrementally with
  a per-device cursor and deduplicates into the `events` hypertable. The worker connects as the DB
  owner (bypasses RLS: trusted backend infrastructure, never user-facing).
- **Frontend** (Vite + React 19 + Mantine v9): multi-tenant shell, device management, monitoring
  dashboard. Typed API client generated from the backend OpenAPI schema.

## Tech stack

| Area | Technologies |
|------|-------------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 |
| Storage | TimescaleDB (Postgres 16 + extension), hypertables for metrics and events, RLS |
| Queue/worker | ARQ 0.28 + Redis |
| Security | argon2 (passwords), Fernet (device secrets), Postgres RLS, SSRF guard |
| Frontend | Vite, React 19, TypeScript, Mantine v9 (+ Mantine Charts), TanStack Query, React Router |
| Testing | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Repository layout

```
backend/         FastAPI API, ARQ worker, OPNsense connector, models, Alembic migrations, tests
frontend/        React/Mantine app (shell, pages, monitoring/, typed API client, tests)
docs/superpowers/  design specs and implementation plans for each phase/milestone
```

## Requirements

- Docker + Docker Compose (for TimescaleDB and Redis)
- Python 3.12+ (with `venv`)
- Node.js 20+ and npm

## Development setup

### 1. Infrastructure (DB + Redis)

```bash
cd backend
docker compose up -d db redis     # TimescaleDB + Redis
```

### 2. Backend (API)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e .

# required environment variables (development example)
export DATABASE_URL=postgresql+asyncpg://opngms_app:opngms_app@localhost:5432/opngms
export ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
export REDIS_URL=redis://localhost:6379
export SESSION_SECRET="<random-string>"
export MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

alembic upgrade head              # apply migrations (uses the owner ADMIN_DATABASE_URL)
uvicorn app.main:app --reload     # API on http://localhost:8000
```

Create the first superadmin with the one-time `POST /api/setup` endpoint.

### 3. Worker (poller + event ingest)

```bash
cd backend
arq app.worker.WorkerSettings     # or: docker compose up worker
```

### 4. Frontend

```bash
cd frontend
npm install
npm run gen:api                   # (re)generate API types from the backend OpenAPI schema
npm run dev                       # dashboard on http://localhost:5173
```

## Tests

```bash
# Backend (needs a reachable test TimescaleDB)
cd backend
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q

# Frontend
cd frontend
npm test            # Vitest
npm run build       # typecheck (tsc) + build
npm run lint        # ESLint
```

## Security & multi-tenancy

- **Per-customer isolation:** every tenant-scoped table carries a `tenant_id` and a `tenant_isolation`
  RLS policy (`ENABLE` + `FORCE`), fail-closed (`NULLIF` when the context is absent). The API sets
  `app.current_tenant` per transaction and connects as the non-superuser `opngms_app` role; the worker
  uses the owner (superuser) for fleet-wide writes. Cross-tenant isolation is covered by SQL-level and
  real-API tests.
- **Device secrets** are encrypted with Fernet (`MASTER_KEY`); never returned or logged (write-only).
- **SSRF guard** on the connector: HTTPS only, no userinfo, DNS resolution + blocking of
  loopback/link-local (incl. cloud metadata 169.254.169.254)/unspecified/multicast/reserved (private
  RFC1918 ranges are allowed), IP pinning, no redirects, sanitized errors.
- **Auth:** server-side sessions + CSRF header on mutations; argon2 password hashing; 4-role RBAC
  (superadmin + tenant_admin/operator/read_only).

## Roadmap & status

| Phase | Scope | Status |
|-------|-------|--------|
| **1 — Foundation & Inventory** | Scaffold, auth/RBAC, org admin, devices/secrets/onboarding, frontend shell | ✅ Done |
| **SSRF hardening** | SSRF guard on the OPNsense connector | ✅ Done |
| **2 — Monitoring** | 2A poller core · 2B network metrics + alerting · 2C metrics/health/alert API + RLS · 2D dashboard frontend | ✅ Done |
| **3 — Log/Event ingest** | Pull-API event ingest into an `events` hypertable (RLS) for reporting. 3A Suricata ✅ · 3B DNS ✅ · 3C query API ✅ | ✅ Done |
| **4 — Config management** | Versioned, encrypted config backup + drift detection (schema-agnostic, RLS). 4A backup+drift ✅ · 4B config model · 4C firewall-aware UI · 4D edit + push | 🔄 In progress (4A ✅) |
| **5 — PDF reporting** | Per-customer PDF reports (attacks, sites visited, bandwidth) | ⬜ Planned |
| **Deploy** | Multi-stage Dockerfile + production docker-compose for the whole stack | ⬜ End of project |

Design specs and implementation plans for each milestone live in
[`docs/superpowers/`](docs/superpowers/).

## License

See [LICENSE](LICENSE).

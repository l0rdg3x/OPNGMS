# OPNGMS — OPNsense Global Management System

A centralized, multi-tenant console for MSPs that manage and monitor a fleet of **OPNsense**
firewalls from a single pane of glass. Device inventory, health and network monitoring, alerting,
log/event ingest, per-customer white-label PDF reporting, and — in progress — configuration push.

> **Status:** Phases 1 (Foundation & Inventory), 2 (Monitoring), 3 (Log/Event ingest), and 5 (PDF
> reporting) are **complete**; Phase 4 (Config management) is **in progress** (4A backup + drift, 4B
> config model + capability discovery, 4C firewall-aware config UI, 4D-a change/push pipeline (dry-run),
> 4D-c config editing UI — done). The only remaining work is **production deployment** (Dockerfile +
> compose). See [Roadmap & status](#roadmap--status).

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
| Security | argon2 (passwords), Fernet (device secrets), Postgres RLS, SSRF guard, defusedxml |
| Reporting | WeasyPrint (HTML/CSS → PDF) + Jinja2 (autoescape) + hand-built SVG charts |
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

## Production deployment

The whole stack runs from a single compose file: TimescaleDB + Redis, a one-shot **migrate** job, the
**API** (uvicorn, connecting as the non-superuser `opngms_app` role → RLS enforced), the **worker** (ARQ,
connecting as the owner), and an **nginx frontend** that serves the SPA and reverse-proxies `/api` to the
API (single origin, so the session cookie + CSRF model works without CORS). The backend image bundles the
WeasyPrint system libraries (pango/cairo) so PDF reporting works.

```bash
# 1. Configure secrets (never commit the resulting .env — it is gitignored)
cp .env.example .env
# Edit .env: set a strong POSTGRES_PASSWORD (and the matching ADMIN_DATABASE_URL password), then:
#   SESSION_SECRET:  python -c "import secrets; print(secrets.token_urlsafe(48))"
#   MASTER_KEY:      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. Build + start the stack
docker compose -f docker-compose.prod.yml up -d --build

# migrate runs `alembic upgrade head` as the owner (creates the schema + the opngms_app role + RLS),
# then the API/worker/frontend start. The API healthcheck hits GET /healthz.

# 3. Create the first superadmin (one-time)
curl -X POST http://localhost/api/setup \
  -H 'Content-Type: application/json' -H 'X-OPNGMS-CSRF: 1' \
  -d '{"email":"admin@example.com","name":"Admin","password":"<strong-password>"}'
```

The app is then served at `http://localhost/`. Notes:
- **TLS** is the operator's responsibility — front the `frontend` service with an HTTPS reverse proxy /
  load balancer for production (the bundled nginx listens on plain HTTP:80).
- The default `opngms_app` DB password is `opngms_app` (set by migration `0003`); change it for real
  deployments (`ALTER ROLE opngms_app PASSWORD '…'` then update `DATABASE_URL`).
- The backend image is pinned to **Python 3.14** (matching the dev/test runtime).

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
- **TLS certificate pinning (SEC-2):** opt-in per device — when a `tls_fingerprint` is set, the connector
  verifies the device's leaf-cert SHA-256 **before sending credentials** (MITM-resistant even with a
  self-signed cert). With no fingerprint, `verify_tls=false` stays permissive (self-signed accepted).
- **Auth:** server-side sessions + CSRF header on mutations; argon2 password hashing; 4-role RBAC
  (superadmin + tenant_admin/operator/read_only); **login rate-limiting** (lockout after N failures) +
  failed-login auditing.
- **Web hardening (SEC-1):** security response headers (CSP, HSTS, X-Frame-Options DENY, nosniff,
  Referrer-Policy, Permissions-Policy) on the API and the nginx SPA; **CORS closed by default**
  (opt-in via `cors_allow_origins`); the app-role DB password is env-configurable (`APP_ROLE_PASSWORD`).
- **Continuous assurance:** a consolidated **application-security test suite**
  (`tests/test_security_suite.py` — CSRF, RLS isolation, SSRF, secret redaction, headers, rate-limit,
  SQL-injection allowlist, XXE) + a **dependency audit** (`scripts/security_audit.sh`: `pip-audit` +
  `npm audit`) wired into **CI** (`.github/workflows/ci.yml`). GitHub security workflows:
  **CodeQL** (static analysis, Python + TS), **Dependabot** + **Dependency Review** (dependency updates +
  PR gate), **Trivy** (container-image CVE scan), a **scheduled weekly audit**, and **gitleaks** (secret
  scanning).

## Roadmap & status

| Phase | Scope | Status |
|-------|-------|--------|
| **1 — Foundation & Inventory** | Scaffold, auth/RBAC, org admin, devices/secrets/onboarding, frontend shell | ✅ Done |
| **SSRF hardening** | SSRF guard on the OPNsense connector | ✅ Done |
| **2 — Monitoring** | 2A poller core · 2B network metrics + alerting · 2C metrics/health/alert API + RLS · 2D dashboard frontend | ✅ Done |
| **3 — Log/Event ingest** | Pull-API event ingest into an `events` hypertable (RLS) for reporting. 3A Suricata ✅ · 3B DNS ✅ · 3C query API ✅ | ✅ Done |
| **4 — Config management** | Versioned, encrypted config backup + drift detection (schema-agnostic, RLS). 4A backup+drift ✅ · 4B config model + capability ✅ · 4C firewall-aware UI ✅ · 4D edit + push (4D-a pipeline ✅, dry-run · 4D-c editing UI ✅) | 🔄 In progress (4A–4C, 4D-a, 4D-c ✅) |
| **5 — PDF reporting** | Per-customer white-label PDF reports (attacks, sites visited, bandwidth). 5A reporting engine (WeasyPrint + Jinja2 + SVG charts, tenant-scoped aggregation, on-demand generate API, Attacks section) · 5B Web Activity (DNS) + Data Usage (bandwidth) + Up/Down status, per-firewall · 5C Applications + Web Filter (labeled sample data) with threat-level color coding · 5D per-tenant white-label config (title/owner/timezone + logo upload, settings UI) · 5E scheduled reports (weekly ARQ cron) + stored history + on-demand generate/download UI · 5F readability (labelled chart axes + units + plain-language section explanations for non-technical customers) · 5G weekly cadence + server-side report i18n (every string translatable, en fallback) · 5H per-tenant report language + full translations (en/it/es/fr/de/pt/nl) | ✅ Done |
| **Deploy** | Production Dockerfiles (backend + WeasyPrint, frontend + nginx) + `docker-compose.prod.yml` for the whole stack (db, redis, migrate, api, worker, frontend) | ✅ Done |

Design specs and implementation plans for each milestone live in
[`docs/superpowers/`](docs/superpowers/).

## License

See [LICENSE](LICENSE).

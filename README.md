# OPNGMS — OPNsense Global Management System

A multi-tenant console for MSPs to **manage and monitor a fleet of [OPNsense](https://opnsense.org/)
firewalls** from a single pane of glass: device inventory, health & network monitoring, alerting,
security/event ingest, per-customer white-label PDF reporting, and configuration backup/drift.

[![CI](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml)
[![Container Image Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml)
[![Secret Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml)

Tenant isolation is **structural**, not advisory: a shared schema with `tenant_id` and Postgres
**Row-Level Security** (`ENABLE` + `FORCE`, fail-closed), with the API running as a non-superuser role.

---

## Features

- **Inventory** — onboard customer firewalls with encrypted API credentials and reachability tests.
- **Monitor** — periodic OPNsense-API polling into TimescaleDB hypertables: health metrics
  (CPU/mem/disk, uptime, firmware), network metrics (interfaces, gateways, VPN), up/down status.
- **Alerting** — threshold-based alerts evaluated on every poll, with an active/historical view.
- **Event ingest** — incremental, deduplicated pull of Suricata IDS/IPS alerts and DNS queries.
- **Reporting** — per-customer white-label PDF reports (attacks, web activity, data usage), scheduled
  weekly or generated on demand, localized per tenant (en/it/es/fr/de/pt/nl).
- **Config management** — versioned, encrypted configuration backup with drift detection and a
  firewall-aware editing UI.
- **Device actions** — trigger firmware updates / major upgrades and plugin install/remove from the
  console, now or scheduled, run by a reboot-tolerant worker; plus a one-click deep-link to the
  device's WebGUI.
- **Configuration templates** — reusable, typed templates (firewall aliases, any introspectable
  OPNsense setting, Suricata/IDS rulesets) in a shared MSP library with per-customer overrides, and
  **profiles** (ordered bundles of templates), applied to a device with a redacted preview, now or
  scheduled.
- **Multi-tenant dashboard** — fleet overview, per-device time-series charts, alert list.

## Architecture

```
              ┌───────────────┐   cron         ┌───────────────┐
              │ ARQ scheduler │───────────────►│ Redis (broker)│
              └───────────────┘  enqueue jobs   └──────┬────────┘
            poll_device / ingest_device_events         │
                                              ┌─────────▼────────┐  OpnsenseClient   ┌──────────┐
                                              │   ARQ worker(s)  │──────HTTPS───────►│ OPNsense │
                                              └─────────┬────────┘  (SSRF-guarded,   │ sys, IDS │
                                                        │           optional TLS pin) └──────────┘
                                                        │ metrics / status / alerts / events
  React + Mantine ──HTTP──► FastAPI ──RLS──►  ┌─────────▼─────────────────────────┐  (owner, RLS-exempt)
  (SPA, nginx)              (opngms_app role)  │ TimescaleDB: metrics & events      │
                                               │ (hypertables) + tenants, devices,  │
                                               │ alerts, sessions, reports, ...     │
                                               └────────────────────────────────────┘
```

- **API** — async FastAPI. Session auth + per-session CSRF, 4-role RBAC, tenant-scoped endpoints.
  Connects as the non-superuser `opngms_app` role, so RLS filters every read per customer.
- **Worker** — ARQ + Redis. Cron jobs enqueue per-device work; `OpnsenseClient` is the single
  outbound HTTP boundary (SSRF guard + optional certificate pinning). The worker connects as the DB
  owner (RLS-exempt: trusted infrastructure, never user-facing).
- **Frontend** — Vite + React 19 + Mantine v9 SPA with a typed API client generated from the backend
  OpenAPI schema, served by nginx which also reverse-proxies `/api` (same origin → no CORS needed).

## Tech stack

| Area | Technologies |
|------|--------------|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 |
| Storage | TimescaleDB (PostgreSQL 16 + extension), hypertables for metrics & events, Row-Level Security |
| Worker | ARQ + Redis |
| Security | argon2 (passwords), Fernet (device secrets), Postgres RLS, SSRF guard, TLS pinning, defusedxml |
| Reporting | WeasyPrint (HTML/CSS → PDF) + Jinja2 (autoescape) + hand-built SVG charts |
| Frontend | Vite, React 19, TypeScript, Mantine v9, TanStack Query, React Router, openapi-fetch |
| Testing | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Repository layout

```
backend/           FastAPI API, ARQ worker, OPNsense connector, models, Alembic migrations, tests
frontend/          React/Mantine SPA (shell, pages, typed API client, tests)
docs/superpowers/  design specs and implementation plans, one per milestone
.github/workflows/ CI + security workflows (tests, audit, CodeQL, Trivy, gitleaks)
```

## Quick start — development

Requirements: Docker + Docker Compose, Python 3.14 (`venv`), Node.js 20+.

```bash
# 1. Infrastructure (TimescaleDB + Redis)
cd backend
docker compose up -d db redis

# 2. API
python -m venv .venv && . .venv/bin/activate
pip install -e .
export DATABASE_URL=postgresql+asyncpg://opngms_app:opngms_app@localhost:5432/opngms
export ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
export REDIS_URL=redis://localhost:6379
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
alembic upgrade head                 # apply migrations (as the owner ADMIN_DATABASE_URL)
uvicorn app.main:app --reload        # API on http://localhost:8000

# 3. Worker (in another shell)
arq app.worker.WorkerSettings

# 4. Frontend
cd ../frontend
npm install
npm run gen:api                      # (re)generate API types from the backend OpenAPI schema
npm run dev                          # SPA on http://localhost:5173
```

Create the first superadmin once via `POST /api/setup`.

## Quick start — production

The whole stack runs from one compose file: TimescaleDB + Redis, a one-shot **migrate** job, the
**API** (uvicorn as `opngms_app` → RLS enforced), the **worker** (ARQ as the owner), and an **nginx
frontend** that serves the SPA and reverse-proxies `/api`. The backend image bundles the WeasyPrint
system libraries so PDF reporting works out of the box.

```bash
cp .env.example .env        # then edit: strong POSTGRES_PASSWORD, SESSION_SECRET, MASTER_KEY (never commit .env)
docker compose -f docker-compose.prod.yml up -d --build
# migrate runs `alembic upgrade head`, then API/worker/frontend start (API healthcheck: GET /healthz).

# Create the first superadmin (one-time):
curl -X POST http://localhost/api/setup \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","name":"Admin","password":"<strong-password>"}'
```

**Behind a reverse proxy.** The stack expects to sit behind one (or several chained) proxies. nginx
forwards `X-Forwarded-Proto` (preserving the original scheme from an upstream TLS-terminating proxy)
and a sanitised `X-Forwarded-For`; uvicorn runs with `--proxy-headers` so the API sees the real client
IP and scheme. To recover the true client IP through an external proxy, set `set_real_ip_from` in
`frontend/nginx.conf`. **TLS** is the operator's responsibility — terminate HTTPS at your edge proxy
(the bundled nginx listens on plain HTTP:80).

## Configuration

Set via environment (see `.env.example`). Highlights:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | App connection — the **non-superuser** `opngms_app` role (RLS applies). |
| `ADMIN_DATABASE_URL` | Owner connection for migrations and the worker (RLS-exempt). |
| `SESSION_SECRET` | Server-side session signing secret. |
| `MASTER_KEY` | Fernet key encrypting device credentials at rest. |
| `MASTER_KEY_OLD_KEYS` | Comma-separated retired keys, decryption-only — used during key rotation. |
| `SESSION_TTL_HOURS` / `SESSION_IDLE_MINUTES` | Absolute and idle session timeouts. |
| `CORS_ALLOW_ORIGINS` | Comma-separated allowed origins; empty = CORS disabled (same-origin). |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_WINDOW_SECONDS` | Login rate-limit / lockout. |
| `INGEST_EVERY_MINUTES`, `CONFIG_BACKUP_HOUR`, `REPORT_WEEKDAY`, `REPORT_HOUR` | Worker cron cadences. |

## Security & multi-tenancy

- **Tenant isolation** — every tenant-scoped table carries a `tenant_id` and a fail-closed RLS policy
  (`ENABLE` + `FORCE`). The API sets `app.current_tenant` per transaction and runs as `opngms_app`;
  cross-tenant isolation is covered by SQL-level and real-API tests.
- **Sessions & CSRF** — opaque session tokens stored only as a SHA-256 hash (a DB dump yields no usable
  sessions); idle + absolute expiry; rotation on login; "log out everywhere" + an active-sessions view;
  an hourly cleanup cron. CSRF uses a per-session token validated in constant time on every mutation.
- **Credentials** — argon2 password hashing; device secrets encrypted with Fernet (`MASTER_KEY`),
  never returned or logged. Rotate with zero downtime: set the new `MASTER_KEY`, move the old key into
  `MASTER_KEY_OLD_KEYS`, deploy, run `python -m app.scripts.rekey_secrets` (as the owner), then clear
  the old key and redeploy.
- **Outbound safety** — SSRF guard on the connector (HTTPS only, no redirects, blocks
  loopback/link-local incl. cloud metadata, private ranges allowed, IP-pinned, sanitised errors), plus
  opt-in **TLS certificate fingerprint pinning** (verified before credentials are sent).
- **Web hardening** — security response headers (CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy,
  Permissions-Policy); CORS closed by default; login rate-limiting that fails closed + failed-login
  auditing; hardened XML parsing (defusedxml).
- **Continuous assurance** — an application-security test suite (CSRF, RLS, SSRF, secret redaction,
  headers, rate-limit, SQL-injection allowlist, XXE) and a dependency audit run in CI, alongside
  CodeQL, Dependabot + Dependency Review, Trivy image scanning, and gitleaks. `main` is protected and
  requires these checks to pass before merge. See [`SECURITY.md`](SECURITY.md) to report a vulnerability.

## Project status

| Area | Status |
|------|--------|
| **Foundation & inventory** — auth/RBAC, org admin, device onboarding, encrypted secrets, SPA shell | ✅ Done |
| **Monitoring** — poller, health + network metrics, alerting, dashboard | ✅ Done |
| **Event ingest** — Suricata IDS + DNS into the `events` hypertable, query API (keyset-paginated) | ✅ Done |
| **PDF reporting** — white-label per-tenant reports, scheduled + on-demand, 7-language localization | ✅ Done |
| **Config management** — encrypted backup + drift detection + firewall-aware editing UI + **live alias push** | ✅ Done¹ |
| **OPNsense connector** — read/telemetry endpoints verified against real OPNsense 26.1.9; **(edition, version)-aware** endpoint matrix (Community / Business) | ✅ Done |
| **Device actions** — firmware update / multi-step major upgrade (reboot-tolerant) + plugin install/remove, now or scheduled, behind a per-device confirm; a "Firmware" UI tab + a WebGUI deep-link button; plugin install/remove verified live on real OPNsense 26.1.9² | ✅ Done |
| **Configuration templates (M1–M3)** — a global MSP **template library** (superadmin-managed) + per-tenant **override** + typed **apply** that reuses the config-push pipeline (preview → now/scheduled → snapshot), and **profiles** (M2): named, **ordered bundles of templates** applied to a device in one shot (fan-out to one change per member). A **kind-pluggable engine** ships four kinds: `firewall_alias` (M1), the **generic `opnsense_setting`** (M3) — any introspectable, fleet-portable OPNsense setting rendered as a **value-controlled** auto-form (hardware/device-specific fields excluded), **`suricata_ruleset`** (M3) — enable a set of Suricata/IDS rulesets picked from the device's live catalog, and **`firewall_rule`** (M3) — a portable "Rules [new]" (MVC) filter rule whose target **interface is chosen at apply time** (empty = floating) so the template stays fleet-portable, idempotently upserted by `(description, interface)`. Superadmin Library + Profiles UI + per-device Apply tabs; live-verified on real OPNsense 26.1.9³ | ✅ Done |
| **Deployment** — production Dockerfiles + `docker-compose.prod.yml`, reverse-proxy aware | ✅ Done |
| **Hardening** — web hardening, TLS pinning, session lifecycle, `MASTER_KEY` rotation, CI security suite, branch protection | ✅ Done |

¹ Live configuration **push** to a device (firewall aliases, 4D-b) is verified against real OPNsense 26.1.9
and enabled behind a default-OFF `LIVE_PUSH_ENABLED` master switch, capturing a pre-apply config snapshot as
a rollback point; automatic rollback is a planned follow-up.

² Plugin install/remove was exercised end-to-end on the real 26.1.9 box (with guaranteed cleanup); firmware
update/upgrade are covered by mocked worker tests only (they reboot the device). True single-sign-on into the
WebGUI is a separate milestone — the button is currently a deep-link to the WebGUI login.

³ Configuration templates are a multi-milestone program: **M1** = the engine + the `firewall_alias` kind;
**M2** = profiles (ordered bundles, fan-out apply); **M3** = the kind-pluggable registries plus three new
kinds — the **generic `opnsense_setting`** (introspection-driven, value-controlled, fleet-portable),
**`suricata_ruleset`** (enable-only IDS rulesets, charset-guarded against path injection), and
**`firewall_rule`** (Rules [new] / MVC filter rules; interface is an apply-time binding so the template
stays portable; idempotent upsert by `(description, interface)`; the engine grew a generic apply-time
`bindings` channel for this, identity-preserving for the other kinds). All merged & live-verified on the
real 26.1.9 box. Next curated kind: monit. The M1 live verify surfaced & fixed a real connector bug —
OPNsense stored a JSON-list alias `content` as the literal `"Array"`; it is now joined to a newline
string (also fixing the manual config-push path).

Design specs and implementation plans for every milestone live in [`docs/superpowers/`](docs/superpowers/).

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
npm run build       # tsc typecheck + production build
npm run lint        # ESLint
```

## License

See [LICENSE](LICENSE).

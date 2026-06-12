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
- **Reporting** — per-customer white-label PDF reports (attacks, web activity, data usage), localized
  per tenant (en/it/es/fr/de/pt/nl), with **email delivery**: per-tenant and per-device schedules
  (weekly / monthly / on-demand), multiple recipients, white-label sender override, and a manual
  "send now" trigger. SMTP is configured by the superadmin in-app; a failed send retries for up to
  2 hours without regenerating the PDF.
- **Config management** — versioned, encrypted configuration backup with drift detection and a
  firewall-aware editing UI.
- **Device actions** — trigger firmware updates / major upgrades and plugin install/remove from the
  console, now or scheduled, run by a reboot-tolerant worker; plus a one-click deep-link to the
  device's WebGUI.
- **Configuration templates** — reusable, **value-controlled** templates in a shared MSP library
  with per-customer overrides, applied with a redacted preview (now or scheduled). Five kinds:
  firewall aliases, any introspectable OPNsense setting, Suricata/IDS rulesets, "Rules [new]" firewall
  rules (interface bound at apply time), and Monit health-check tests — plus **profiles** (ordered
  bundles of templates).
- **Two-factor auth** — optional/enforceable **TOTP** login with recovery codes, a superadmin
  enforcement policy, and superadmin / break-glass recovery.
- **Multi-tenant dashboard** — fleet overview, per-device time-series charts, alert list.

## Screenshots

A dark, instrument-grade "operations console" UI (Mantine + IBM Plex), built for SOC/NOC workflows.

| Sign in | Fleet overview |
|---|---|
| [![Login](docs/ui/login.png)](docs/ui/login.png) | [![Overview](docs/ui/overview.png)](docs/ui/overview.png) |

| Template library | New-template (kind picker) |
|---|---|
| [![Template library](docs/ui/template-library.png)](docs/ui/template-library.png) | [![New template](docs/ui/template-modal.png)](docs/ui/template-modal.png) |

| Two-factor login (TOTP) | Two-factor settings & policy |
|---|---|
| [![MFA login step](docs/ui/mfa-login.png)](docs/ui/mfa-login.png) | [![MFA settings](docs/ui/mfa-security.png)](docs/ui/mfa-security.png) |

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
| Security | argon2 (passwords), Fernet (device secrets), TOTP MFA (pyotp), Postgres RLS, SSRF guard, TLS pinning, defusedxml |
| Reporting | WeasyPrint (HTML/CSS → PDF) + Jinja2 (autoescape) + hand-built SVG charts |
| Frontend | Vite, React 19, TypeScript, Mantine v9, TanStack Query, React Router, openapi-fetch |
| Testing | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Repository layout

```
backend/             FastAPI API, ARQ worker, OPNsense connector, models, Alembic migrations, tests
frontend/            React/Mantine SPA (shell, pages, typed API client, tests); nginx/ = mode-aware serving
docs/superpowers/    design specs and implementation plans, one per milestone
deploy/              Caddy config for the automatic-HTTPS override
docker-compose*.yml  base prod stack + TLS overrides (tls / caddy / traefik)
.github/workflows/   CI + security workflows (tests, audit, CodeQL, Trivy, gitleaks)
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
npm install --legacy-peer-deps       # (a peer-dep range conflict requires --legacy-peer-deps)
npm run gen:api                      # (re)generate API types from the backend OpenAPI schema
npm run dev                          # SPA on http://localhost:5173
```

Create the first superadmin once via `POST /api/setup`. When MFA is enrolled, log in in two steps
(`POST /api/login` → `POST /api/login/mfa`); a locked-out superadmin recovers with
`python -m app.cli mfa-reset --email <email>` on the host.

## Deployment (production)

One compose stack: TimescaleDB + Redis, a one-shot **migrate** job, the **API** (uvicorn as
`opngms_app` → RLS enforced), the **worker** (ARQ as the owner), and an **nginx frontend** (SPA +
`/api` reverse-proxy). The backend image bundles the WeasyPrint system libraries so PDF reporting
works out of the box.

Every credential is env-driven and **must be changed** — the API **refuses to start** while any secret
(`DATABASE_URL`/`ADMIN_DATABASE_URL` passwords, `SESSION_SECRET`, `MASTER_KEY`, `APP_ROLE_PASSWORD`)
still holds its `change-me-*` placeholder. Keep the DB password matched between `DATABASE_URL` and
`APP_ROLE_PASSWORD` (the app role), and between `ADMIN_DATABASE_URL` and `POSTGRES_PASSWORD` (the owner).

```bash
cp .env.example .env   # edit: strong POSTGRES_PASSWORD/APP_ROLE_PASSWORD, SESSION_SECRET, MASTER_KEY,
                       #       and the frontend/TLS block (SERVER_NAME / DOMAIN / ACME_EMAIL). Never commit .env.
```

The SPA carries logins, MFA and `Secure` cookies, so **serve it over HTTPS** in production (plain HTTP
is for localhost/dev only). Pick **exactly one** of the four models below — the overrides are mutually
exclusive (Docker Compose **v2.24.4+** is required for the `!override` tag they use):

**1. Behind your own reverse proxy / load balancer (recommended).** The base stack serves the SPA as
plain HTTP bound to **`127.0.0.1:8080`** — not internet-facing. Put your TLS terminator (Cloudflare, a
cloud LB, an existing nginx/Caddy/Traefik, a Kubernetes ingress) in front, forwarding to
`127.0.0.1:8080` with `X-Forwarded-Proto: https`.
```bash
docker compose -f docker-compose.prod.yml up -d --build
```

**2. Self-contained HTTPS with your own certificate.** nginx terminates TLS itself (80 → 443
redirect). Drop `fullchain.pem` + `privkey.pem` into `./certs` (`CERT_DIR`) and set `SERVER_NAME`; if
no cert is present a self-signed one is generated at startup so the server still boots.
```bash
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml up -d --build
```

**3. Self-contained HTTPS with automatic certificates (Let's Encrypt).** A proxy in front
auto-obtains + auto-renews a real cert. Point `DOMAIN`'s DNS at the host (ports 80/443 reachable) and
set `DOMAIN` + `ACME_EMAIL`. Choose **Caddy** *or* **Traefik** (Traefik is the same controller many
already run on Docker & Kubernetes) — never both:
```bash
docker compose -f docker-compose.prod.yml -f docker-compose.caddy.yml   up -d --build   # Caddy
docker compose -f docker-compose.prod.yml -f docker-compose.traefik.yml up -d --build   # Traefik
```

| # | Files | TLS terminated by | Certificate | Host ports |
|---|-------|-------------------|-------------|------------|
| 1 | base only | your edge proxy / LB / ingress | yours (upstream) | `127.0.0.1:8080` (HTTP) |
| 2 | `+ docker-compose.tls.yml` | the bundled nginx | yours (mount PEM in `./certs`) | `80`→`443` |
| 3a | `+ docker-compose.caddy.yml` | bundled Caddy | Let's Encrypt (auto) | `80` + `443` |
| 3b | `+ docker-compose.traefik.yml` | bundled Traefik | Let's Encrypt (auto) | `80` + `443` |

**Create the first superadmin** (one-time) against your public URL (or `http://127.0.0.1:8080` in
model 1):
```bash
curl -X POST https://<your-domain>/api/setup -H 'Content-Type: application/json' \
  -d '{"email":"admin@example.com","name":"Admin","password":"<strong-password>"}'
```
Then enrol MFA under **Two-factor auth** (two-step login: `POST /api/login` → `POST /api/login/mfa`; a
locked-out superadmin recovers with `python -m app.cli mfa-reset --email <email>` on the host).

**Real client IP through a proxy.** nginx forwards `X-Forwarded-Proto` (preserving the upstream
HTTPS scheme so `Secure` cookies work) and a sanitised `X-Forwarded-For`; uvicorn runs with
`--proxy-headers`. To recover the true client IP behind an external proxy (for the login
rate-limit/lockout), set `set_real_ip_from` in `frontend/nginx/snippets/app.conf`.

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
| *(in-app)* | **SMTP for report email delivery** is configured by the superadmin under *Admin → SMTP delivery* (not an env var). The password is encrypted at rest with `MASTER_KEY`. An hourly worker cron fires due report schedules; failed sends retry every 10 min for up to 2 h. |

## Security & multi-tenancy

- **Tenant isolation** — every tenant-scoped table carries a `tenant_id` and a fail-closed RLS policy
  (`ENABLE` + `FORCE`). The API sets `app.current_tenant` per transaction and runs as `opngms_app`;
  cross-tenant isolation is covered by SQL-level and real-API tests.
- **Sessions & CSRF** — opaque session tokens stored only as a SHA-256 hash (a DB dump yields no usable
  sessions); idle + absolute expiry; rotation on login; "log out everywhere" + an active-sessions view;
  an hourly cleanup cron. CSRF uses a per-session token validated in constant time on every mutation.
- **Two-factor auth (TOTP)** — optional/enforceable **TOTP** second factor with one-time **recovery
  codes**. Self-service enrollment (QR + password re-auth); the secret is encrypted at rest
  (`MASTER_KEY`), recovery codes are argon2-hashed and single-use (atomic consume), and TOTP is
  anti-replay (last-used step, row-locked). Two-step login uses a short-lived `mfa_pending` session
  upgraded to a fresh full session on success (anti-fixation), rate-limited and fail-closed. A
  superadmin policy (`off` / `all` / `privileged`) can **require** MFA, gating non-enrolled users into
  a fail-closed setup-only session until they enroll. Superadmins can **reset** another user's MFA, and
  a host-level **break-glass CLI** (`python -m app.cli mfa-reset --email <e>`, audited) recovers the
  last locked-out superadmin.
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
| **Configuration templates (M1–M3)** — a global MSP **template library** (superadmin-managed) + per-tenant **override** + typed **apply** that reuses the config-push pipeline (preview → now/scheduled → snapshot), and **profiles** (M2): named, **ordered bundles of templates** applied to a device in one shot (fan-out to one change per member). A **kind-pluggable engine** ships five kinds: `firewall_alias` (M1), the **generic `opnsense_setting`** (M3) — any introspectable, fleet-portable OPNsense setting rendered as a **value-controlled** auto-form (hardware/device-specific fields excluded), **`suricata_ruleset`** (M3) — enable a set of Suricata/IDS rulesets picked from the device's live catalog, **`firewall_rule`** (M3) — a portable "Rules [new]" (MVC) filter rule whose target **interface is chosen at apply time** (empty = floating) so the template stays fleet-portable, idempotently upserted by `(description, interface)`, and **`monit_test`** (M3) — a portable Monit health-check test (condition + action) upserted by `name`. Superadmin Library + Profiles UI + per-device Apply tabs; live-verified on real OPNsense 26.1.9³ | ✅ Done |
| **Login MFA (TOTP)** — TOTP second factor + one-time recovery codes; self-enroll + superadmin enforcement policy (off/all/privileged) with a fail-closed setup gate; two-step login (pending→full session); superadmin reset of a user's MFA + a host **break-glass CLI**; adversarially security-reviewed | ✅ Done |
| **Report email delivery & scheduling** — per-tenant and per-device schedules (weekly/monthly/on-demand) each with a UTC hour and a list of recipient emails; superadmin global SMTP relay (host/port/security/credentials, encrypted at rest); per-tenant white-label sender override; manual "send now" trigger; an hourly cron fires due schedules; send failures retry every 10 min for up to 2 h without re-rendering the PDF | ✅ Done |
| **Deployment** — production Dockerfiles + a base `docker-compose.prod.yml` (frontend HTTP, localhost-bound, safe-by-default) with override files for every TLS model: behind your reverse proxy / LB, built-in TLS (your cert, self-signed fallback), or automatic Let's Encrypt via **Caddy** or **Traefik** | ✅ Done |
| **Hardening** — web hardening, TLS pinning, session lifecycle, `MASTER_KEY` rotation, CI security suite, branch protection | ✅ Done |

¹ Live configuration **push** to a device (firewall aliases, 4D-b) is verified against real OPNsense 26.1.9
and enabled behind a default-OFF `LIVE_PUSH_ENABLED` master switch, capturing a pre-apply config snapshot as
a rollback point; automatic rollback is a planned follow-up.

² Plugin install/remove was exercised end-to-end on the real 26.1.9 box (with guaranteed cleanup); firmware
update/upgrade are covered by mocked worker tests only (they reboot the device). True single-sign-on into the
WebGUI is a separate milestone — the button is currently a deep-link to the WebGUI login.

³ Configuration templates are a multi-milestone program: **M1** = the engine + the `firewall_alias` kind;
**M2** = profiles (ordered bundles, fan-out apply); **M3** = the kind-pluggable registries plus four new
kinds — the **generic `opnsense_setting`** (introspection-driven, value-controlled, fleet-portable),
**`suricata_ruleset`** (enable-only IDS rulesets, charset-guarded against path injection),
**`firewall_rule`** (Rules [new] / MVC filter rules; interface is an apply-time binding so the template
stays portable; idempotent upsert by `(description, interface)`; the engine grew a generic apply-time
`bindings` channel for this, identity-preserving for the other kinds — **profile** apply threads the same
channel, so a `firewall_rule` member can be bound to one interface for the whole profile application
instead of always floating), and **`monit_test`** (portable
Monit health-check tests — condition + action — upserted by `name`; services are intentionally excluded
as they reference per-device UUIDs). All merged & live-verified on the real 26.1.9 box. The M1 live verify
surfaced & fixed a real connector bug — OPNsense stored a JSON-list alias `content` as the literal
`"Array"`; it is now joined to a newline string (also fixing the manual config-push path).

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

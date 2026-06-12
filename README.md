# OPNGMS — OPNsense Global Management System

A multi-tenant console for MSPs to **manage and monitor a fleet of [OPNsense](https://opnsense.org/)
firewalls** from a single pane of glass: device inventory, health & network monitoring, alerting,
security/event ingest, per-customer white-label PDF reporting **with scheduled email delivery**,
configuration templates, and configuration backup/drift.

[![CI](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/ci.yml)
[![Container Image Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/trivy.yml)
[![Secret Scan](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/l0rdg3x/OPNGMS/actions/workflows/gitleaks.yml)

Tenant isolation is **structural**, not advisory: a shared schema with `tenant_id` and Postgres
**Row-Level Security** (`ENABLE` + `FORCE`, fail-closed), with the API running as a non-superuser role.

---

## Contents

- [Features](#features)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Repository layout](#repository-layout)
- [Quick start — development](#quick-start--development)
- [Deployment (production)](#deployment-production) ← **the production guide**
- [Log lake (optional)](#log-lake-optional)
- [Configuration reference](#configuration-reference)
- [Security & multi-tenancy](#security--multi-tenancy)
- [Project status](#project-status)
- [Tests](#tests)
- [License](#license)

---

## Features

- **Inventory** — onboard customer firewalls with encrypted API credentials and reachability tests.
- **Monitor** — periodic OPNsense-API polling into TimescaleDB hypertables: health metrics
  (CPU/mem/disk, uptime, firmware), network metrics (interfaces, gateways, VPN), up/down status.
- **Alerting** — threshold-based alerts evaluated on every poll, with an active/historical view.
- **Event ingest** — incremental, deduplicated pull of Suricata IDS/IPS alerts and DNS queries.
- **Reporting** — per-customer white-label PDF reports (attacks, web activity, data usage), localized
  per tenant (en/it/es/fr/de/pt/nl).
- **Report email delivery** — schedule reports per **tenant** (whole fleet) **and per device** (one
  site), on a **weekly / monthly / on-demand** cadence, each with its own list of recipient emails.
  One **superadmin-configured SMTP relay** (credentials encrypted at rest) sends them; tenants can set
  a **white-label sender** address. A **"send now"** button triggers an immediate delivery, and a
  failed send **retries every 10 min for up to 2 h** without regenerating the PDF.
- **Config management** — versioned, encrypted configuration backup with drift detection (snapshot
  history plus an on-demand **live-vs-applied-template** check), targeted **revert** of an applied
  change, and a firewall-aware editing UI.
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
- **Log lake** (optional) — managed firewalls ship their syslog over **mTLS** to an in-stack
  syslog-ng receiver that indexes into **OpenSearch**. Enable/rotate/revoke forwarding per device from
  the UI (with a cert-expiry + "last log received" liveness indicator); investigate logs from a
  tenant-scoped **Logs** page (Lucene query + filters, unbounded deep paging); and watch the whole
  estate from a superadmin **Log fleet** dashboard (per-tenant forwarding status, ingest health,
  selectable 24h/7d/30d volume, per-device drill-down, CSV/PDF export, and proactive silent-tenant
  alerting by email + banner).
- **Multi-tenant dashboard** — fleet overview, per-device time-series charts, alert list.

## Screenshots

A dark, instrument-grade "operations console" UI (Mantine v9 + IBM Plex), built for SOC/NOC workflows.

| Sign in | Fleet overview |
|---|---|
| [![Login](docs/ui/login.png)](docs/ui/login.png) | [![Overview](docs/ui/overview.png)](docs/ui/overview.png) |

| Report delivery schedule (fleet + per-device) | SMTP delivery (superadmin) |
|---|---|
| [![Report schedule](docs/ui/report-schedule.png)](docs/ui/report-schedule.png) | [![SMTP settings](docs/ui/smtp.png)](docs/ui/smtp.png) |

| Per-tenant report settings (branding & sender) | Configuration templates |
|---|---|
| [![Report settings](docs/ui/report-settings.png)](docs/ui/report-settings.png) | [![Template library](docs/ui/template-library.png)](docs/ui/template-library.png) |

| Two-factor login (TOTP) | Two-factor settings & policy |
|---|---|
| [![MFA login step](docs/ui/mfa-login.png)](docs/ui/mfa-login.png) | [![MFA settings](docs/ui/mfa-security.png)](docs/ui/mfa-security.png) |

## Architecture

```
              ┌───────────────┐   cron         ┌───────────────┐
              │ ARQ scheduler │───────────────►│ Redis (broker)│
              └───────────────┘  enqueue jobs   └──────┬────────┘
   poll_device / ingest_device_events / enqueue_due_reports     │
                                              ┌─────────▼────────┐  OpnsenseClient   ┌──────────┐
                                              │   ARQ worker(s)  │──────HTTPS───────►│ OPNsense │
                                              └────┬──────┬──────┘  (SSRF-guarded,   │ sys, IDS │
                                       PDF reports │      │ aiosmtplib  TLS pin)      └──────────┘
                                          ┌────────▼──┐  ┌▼───────────────┐
                                          │ WeasyPrint│  │ SMTP relay     │──► report recipients
                                          └───────────┘  └────────────────┘
                                                        │ metrics / status / alerts / events
  React + Mantine ──HTTP──► FastAPI ──RLS──►  ┌─────────▼─────────────────────────┐  (owner, RLS-exempt)
  (SPA, nginx)              (opngms_app role)  │ TimescaleDB: metrics & events      │
                                               │ (hypertables) + tenants, devices,  │
                                               │ alerts, sessions, reports,         │
                                               │ smtp_settings, report_schedule, …  │
                                               └────────────────────────────────────┘
```

- **API** — async FastAPI. Session auth + per-session CSRF, 4-role RBAC, tenant-scoped endpoints.
  Connects as the non-superuser `opngms_app` role, so RLS filters every read per customer.
- **Worker** — ARQ + Redis. Cron jobs enqueue per-device work; an **hourly cron fires due report
  schedules** (generate → store → email, with send-retry). `OpnsenseClient` is the single outbound
  HTTP boundary (SSRF guard + optional certificate pinning). The worker connects as the DB owner
  (RLS-exempt: trusted infrastructure, never user-facing).
- **Frontend** — Vite + React 19 + Mantine v9 SPA with a typed API client generated from the backend
  OpenAPI schema, served by nginx which also reverse-proxies `/api` (same origin → no CORS needed).

## Tech stack

| Area | Technologies |
|------|--------------|
| Backend | Python 3.14, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 |
| Storage | TimescaleDB (PostgreSQL 16 + extension), hypertables for metrics & events, Row-Level Security; **OpenSearch** (Apache-2.0) for the optional log lake |
| Worker | ARQ + Redis |
| Email | aiosmtplib (STARTTLS / implicit TLS / plain), Fernet-encrypted SMTP credentials |
| Security | argon2 (passwords), Fernet (device & SMTP secrets), TOTP MFA (pyotp), Postgres RLS, SSRF guard, TLS pinning, defusedxml |
| Reporting | WeasyPrint (HTML/CSS → PDF) + Jinja2 (autoescape) + hand-built SVG charts |
| Frontend | Vite, React 19, TypeScript, Mantine v9, TanStack Query, React Router, openapi-fetch |
| Testing | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Repository layout

```
backend/             FastAPI API, ARQ worker, OPNsense connector, models, Alembic migrations, tests
frontend/            React/Mantine SPA (shell, pages, typed API client, tests); nginx/ = mode-aware serving
docs/superpowers/    design specs and implementation plans, one per milestone
docs/ui/             UI screenshots used in this README
deploy/              Caddy config for the automatic-HTTPS override
docker-compose*.yml  base prod stack + TLS overrides (tls / caddy / traefik)
.env.example         every deployment variable, documented
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

# 3. Worker (in another shell, same env)
arq app.worker.WorkerSettings

# 4. Frontend
cd ../frontend
npm install --legacy-peer-deps       # (a peer-dep range conflict requires --legacy-peer-deps)
npm run gen:api                      # (re)generate API types from the backend OpenAPI schema
npm run dev                          # SPA on http://localhost:5173 (proxies /api → :8000)
```

Create the first superadmin once via `POST /api/setup`. When MFA is enrolled, log in in two steps
(`POST /api/login` → `POST /api/login/mfa`); a locked-out superadmin recovers with
`python -m app.cli mfa-reset --email <email>` on the host.

---

## Deployment (production)

The production stack is **one Docker Compose project** of six services:

| Service | Role |
|---------|------|
| `db` | TimescaleDB (PostgreSQL 16 + extension) — the only stateful service (named volume `opngms_pg`). |
| `redis` | ARQ broker. |
| `migrate` | One-shot: runs `alembic upgrade head` as the **owner**, creating the schema, the non-superuser `opngms_app` role, RLS policies and grants. Runs to completion before `api`/`worker` start. |
| `api` | uvicorn, connects as **`opngms_app`** (RLS enforced), behind `--proxy-headers`. Not published to the host — only the frontend reaches it on the compose network. |
| `worker` | ARQ as the **owner** — polling, ingest, backups, and the hourly **report-delivery** cron. |
| `frontend` | nginx serving the SPA and reverse-proxying `/api` to `api` (single origin → no CORS). |

The backend image bundles the WeasyPrint system libraries (PDF rendering) and `tzdata`; aiosmtplib
ships with the Python dependencies. SMTP is **not** a container — it is an external relay you configure
in the app after the first login (see *First run*).

### Prerequisites

- A Linux host with **Docker Engine** and **Docker Compose v2.24.4+** (the TLS override files use the
  `!override` YAML tag, which needs that version). Check with `docker compose version`.
- For **automatic** TLS (models 3a/3b): a public DNS name pointing at the host, with ports **80 and
  443** reachable from the internet (Let's Encrypt validation).
- Outbound HTTPS from the host to each managed OPNsense box, and outbound SMTP to your mail relay.

### Step 1 — Configure the environment

```bash
cp .env.example .env      # then edit .env — never commit it (.env is gitignored)
```

Set strong, unique values. Two password pairs **must match**, or the app won't connect:

| Set this… | …to match this | Why |
|-----------|----------------|-----|
| `DATABASE_URL` password | `APP_ROLE_PASSWORD` | the API logs in as `opngms_app`; migration creates that role from `APP_ROLE_PASSWORD`. |
| `ADMIN_DATABASE_URL` password | `POSTGRES_PASSWORD` | the worker/migrate log in as the owner the DB container creates. |

Also generate fresh secrets:

```bash
python -c "import secrets; print('SESSION_SECRET=' + secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print('MASTER_KEY=' + Fernet.generate_key().decode())"
```

> **Fail-closed guard.** The API **refuses to start** while any of `DATABASE_URL` /
> `ADMIN_DATABASE_URL` passwords, `SESSION_SECRET`, `MASTER_KEY`, or `APP_ROLE_PASSWORD` still contains
> the shipped `change-me` placeholder. This is intentional — you cannot accidentally run on defaults.

Optional but recommended:

- **`TZ`** — an IANA timezone (e.g. `Europe/Rome`) applied to all containers so logs read in your
  local time. Data is always stored in **UTC**, and report-schedule **hours are interpreted in UTC by
  design** — changing `TZ` does *not* shift when a report is sent, only how log timestamps appear.
- The **frontend/TLS block** (`SERVER_NAME`, `DOMAIN`, `ACME_EMAIL`, `CERT_DIR`, ports) — fill in the
  fields for the model you pick in Step 2.

### Step 2 — Choose how TLS is terminated

The SPA carries logins, MFA and **`Secure` cookies**, so it **must be served over HTTPS** in
production (plain HTTP works only on localhost/dev — browsers drop `Secure` cookies over HTTP on a real
domain, which breaks login). Pick **exactly one** model — the override files are mutually exclusive:

| # | Compose files | TLS terminated by | Certificate | Published host ports |
|---|---------------|-------------------|-------------|----------------------|
| **1** | base only | **your** edge proxy / LB / ingress | yours (upstream) | `127.0.0.1:8080` (HTTP, localhost-only) |
| **2** | `+ docker-compose.tls.yml` | the bundled **nginx** | yours — mount `fullchain.pem` + `privkey.pem` in `./certs` | `80` → `443` |
| **3a** | `+ docker-compose.caddy.yml` | bundled **Caddy** | Let's Encrypt (automatic) | `80` + `443` |
| **3b** | `+ docker-compose.traefik.yml` | bundled **Traefik** | Let's Encrypt (automatic) | `80` + `443` |

- **Model 1 — behind your own reverse proxy / LB (recommended for most.)** The base stack serves the
  SPA as plain HTTP bound to **`127.0.0.1:8080`**, never internet-facing. Put your TLS terminator
  (Cloudflare, a cloud load balancer, an existing nginx/Caddy/Traefik, a Kubernetes ingress) in front,
  forwarding to `127.0.0.1:8080` with the header **`X-Forwarded-Proto: https`** (so the app issues
  `Secure` cookies).
- **Model 2 — built-in TLS with your own certificate.** nginx terminates TLS itself (with an HTTP→HTTPS
  redirect). Drop your PEM files into `CERT_DIR` (`./certs`) and set `SERVER_NAME`. If no certificate is
  present, a **self-signed** one is generated at startup so the server still boots.
- **Model 3 — built-in TLS with automatic certificates.** A bundled **Caddy** *or* **Traefik** obtains
  and auto-renews a real Let's Encrypt certificate. Set `DOMAIN` + `ACME_EMAIL` and point DNS at the
  host. Choose one controller (Traefik is the same one many already run on Docker/Kubernetes).

### Step 3 — Build and start

Run the command for the model you chose (the first run builds the images; `migrate` applies the schema
before `api`/`worker` come up):

```bash
# Model 1 — behind your proxy / LB
docker compose -f docker-compose.prod.yml up -d --build

# Model 2 — built-in TLS, your cert
docker compose -f docker-compose.prod.yml -f docker-compose.tls.yml up -d --build

# Model 3a — automatic TLS via Caddy
docker compose -f docker-compose.prod.yml -f docker-compose.caddy.yml up -d --build

# Model 3b — automatic TLS via Traefik
docker compose -f docker-compose.prod.yml -f docker-compose.traefik.yml up -d --build
```

Watch it come up and confirm health:

```bash
docker compose -f docker-compose.prod.yml ps          # api should be "healthy"
docker compose -f docker-compose.prod.yml logs -f api  # follow logs (Ctrl-C to stop)
```

### Step 4 — First run

Replace `https://<your-domain>` with `http://127.0.0.1:8080` if you're on Model 1 without a proxy yet.

1. **Create the first superadmin** (one-time; refuses if any user already exists):
   ```bash
   curl -X POST https://<your-domain>/api/setup -H 'Content-Type: application/json' \
     -d '{"email":"admin@example.com","name":"Admin","password":"<strong-password>"}'
   ```
   > Use a real email domain — addresses on reserved TLDs (`.local`, `.internal`, `.test`, …) are
   > rejected by RFC-compliant validation.
2. **Sign in** and, under **Two-factor auth**, enrol TOTP and (optionally) set the enforcement policy
   (`off` / `all` / `privileged`).
3. **Configure SMTP delivery** under **Admin → SMTP delivery**: host, port, security
   (STARTTLS / TLS / none), username, password, the default *from* address and name, then **enable
   delivery**. Use **Send a test email** to verify before saving real recipients. The password is
   encrypted at rest with `MASTER_KEY` and is never returned by the API.
4. **Onboard tenants and devices**, then per tenant set **Report settings** (title, logo, language,
   and an optional white-label *sender* address that overrides the global one) and **Report schedule**
   — a fleet schedule plus per-device schedules, each weekly/monthly/on-demand with its own recipients.
   Use **Send now** to deliver immediately.

### Operations

- **Upgrades & migrations.** Pull the new code and re-run your `up` command with `--build`:
  ```bash
  git pull && docker compose -f docker-compose.prod.yml up -d --build
  ```
  The one-shot `migrate` service runs `alembic upgrade head` automatically before `api`/`worker`
  restart, so new database migrations are applied on every deploy.
- **Backups.** All durable state is in the `opngms_pg` volume. Take logical backups with:
  ```bash
  docker compose -f docker-compose.prod.yml exec db \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > opngms-$(date +%F).sql
  ```
- **`MASTER_KEY` rotation (zero downtime).** Set a new `MASTER_KEY`, move the old key into
  `MASTER_KEY_OLD_KEYS`, deploy, run `python -m app.scripts.rekey_secrets` (as the owner) to
  re-encrypt device & SMTP secrets, then clear the old key and redeploy.
- **Timezone.** Set `TZ` in `.env` and redeploy; it affects container/log time only (see Step 1).
- **Real client IP behind a proxy.** nginx forwards `X-Forwarded-Proto` (preserving the upstream HTTPS
  scheme so `Secure` cookies work) and a sanitised `X-Forwarded-For`; uvicorn runs with
  `--proxy-headers`. To recover the true client IP behind an *external* proxy (for the login
  rate-limit/lockout), set `set_real_ip_from` in `frontend/nginx/snippets/app.conf`.
- **Logs.** `docker compose -f docker-compose.prod.yml logs -f worker` shows report-delivery activity;
  delivery successes/failures are also written to the in-app audit log.

### Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| API container exits immediately with a "refusing to start" message | A secret still says `change-me`. Set real values in `.env` (Step 1). |
| Login succeeds via curl but the browser keeps returning to the sign-in page | The SPA is served over plain HTTP on a real domain → `Secure` cookies are dropped. Terminate TLS (Step 2) and forward `X-Forwarded-Proto: https`. |
| `docker compose` errors on the `!override` tag | Compose is older than **v2.24.4**. Upgrade Docker Compose. |
| Scheduled reports never arrive | SMTP not enabled/incorrect (use **Send a test email**); or the schedule is disabled / has no recipients. Check the worker logs and the audit log. |
| Let's Encrypt won't issue a cert (Model 3) | DNS for `DOMAIN` must resolve to the host and ports 80/443 must be reachable from the internet. |

## Log lake (optional)

> **What it is.** An opt-in push-based log pipeline that complements the existing API-pull event
> ingest: managed OPNsense boxes ship their syslog stream (system, filterlog, Suricata EVE JSON) over
> **mTLS** to an in-stack **syslog-ng** receiver, which indexes every message into **OpenSearch**
> (plain HTTP, security plugin disabled, **not published to the host** — internal network only). The
> result is a per-tenant, per-device full log lake for forensic incident analysis, searchable from an
> in-app **Logs** investigation page.
>
> The existing API-pull ingest (Suricata alerts, DNS) continues to work unchanged; the log lake is an
> additional, orthogonal data path.

### How it works

- Each device is enrolled from the **device page → "Log forwarding" tab** (Enable/Disable behind a
  confirm), or via `POST /api/tenants/{tenant}/devices/{device}/log-forwarding/enable`. Enrolling
  issues a **per-device mTLS client certificate** (CN = device ID, O = tenant ID), imports it into the
  OPNsense box, and configures a TLS syslog target pointing at `SYSLOG_RECEIVER_HOST:SYSLOG_TLS_PORT`.
  The tab also shows the certificate expiry and a **liveness** indicator ("last log received") so an
  operator can confirm logs are actually flowing.
- syslog-ng verifies every incoming connection against the OPNGMS CA — only CA-signed client certs are
  accepted. The CN and O RDNs are extracted from the peer certificate to stamp `device_id` and
  `tenant_id` on every indexed document, with no reliance on user-supplied headers.
- Suricata EVE JSON messages are parsed inline; raw syslog lines (filterlog, dhcpd, etc.) pass through
  with the full message text preserved.
- A disk-buffer (256 MiB) on the syslog-ng side absorbs log bursts during OpenSearch restarts — no
  logs are dropped for transient outages within the buffer window.
- Daily indices (`opngms-logs-YYYY.MM.DD`) simplify retention management via ISM policies (Phase 2).

### Searching the logs

The browser **never** talks to OpenSearch. A tenant-scoped, backend-mediated search powers an in-app
**Logs** page (tenant admins and operators only — `read_only` is excluded):

- `POST /api/tenants/{tenant}/logs/search` accepts a time range, an optional device filter, and a
  guarded **Lucene `query_string`** (e.g. `action:block AND src_ip:10.0.0.1`). The backend **always**
  injects the `tenant_id` filter from the RBAC-verified path — a crafted query can never widen past the
  caller's tenant. Leading wildcards are disabled, page size and the time range are capped, and deep
  paging is rejected before reaching OpenSearch.
- The Logs page renders matches in a table (time, device, program, message) and opens the full raw
  document on row click for forensic detail.

### Bring it up

The log lake is an opt-in **overlay** on top of the base production stack:

```bash
# First: edit .env and add the log-lake variables (see the Log lake block in .env.example).
# Then bring up the full stack (or add to an already-running one):
docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml up -d
```

The overlay adds three services:

| Service | Role |
|---------|------|
| `opensearch` | Single-node OpenSearch 2.x, security disabled, plain HTTP, **internal only** (no published port). |
| `syslog-bootstrap` | One-shot: generates the CA + server keypair, provisions per-device client certs, writes them to the `opngms_syslog_certs` volume. |
| `syslog-ng` | mTLS syslog receiver on `SYSLOG_TLS_PORT` (default 6514); forwards to OpenSearch with disk-buffering. |

### Network requirements

- `SYSLOG_TLS_PORT` (default **6514**) must be **reachable by the managed devices** (open in the
  host firewall / cloud security group). It is the only published port added by this overlay.
- OpenSearch port 9200 is **not published** — it is only reachable on the internal Compose network.
  The syslog-ng receiver and the OPNGMS backend reach it at `OPENSEARCH_URL=http://opensearch:9200`.

### High availability (multi-node OpenSearch)

For production resilience, run a 3-node OpenSearch cluster **instead of** the single-node overlay
(the two are alternatives — do **not** combine them):

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.logs.multinode.yml up -d
```

`docker-compose.logs.multinode.yml` starts `opensearch-n1/-n2/-n3` (one cluster, each on its own data
volume) plus the same `syslog-bootstrap` + `syslog-ng` services pointing at `opensearch-n1`. Apply the
replicated template `deploy/opensearch/index-template.multinode.json` (**2 shards, 1 replica**) so each
shard has a copy on another node — losing one node keeps every index green and queryable. The cluster
stays internal-only (no published port; same trust boundary as the single-node overlay). HA behaviour
(node loss → index stays available) is verified at the staging bring-up, alongside the syslog-ng
field-shape check and CRL enforcement.

### Configuration variables (`.env`)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SYSLOG_RECEIVER_HOST` | `logs.opngms.example` | Public hostname/IP the devices connect to (informational; used when issuing client-cert configs to boxes). |
| `SYSLOG_TLS_PORT` | `6514` | mTLS receiver port, published to the host. |
| `OPENSEARCH_URL` | `http://opensearch:9200` | Internal OpenSearch endpoint; no auth. |
| `LOG_RETENTION_DAYS` | `30` | Target retention in days (enforced via ISM policy in Phase 2; informational for Phase 1). |

---

## Configuration reference

Set via environment (see [`.env.example`](.env.example) for the full, documented list). Highlights:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | App connection — the **non-superuser** `opngms_app` role (RLS applies). |
| `ADMIN_DATABASE_URL` | Owner connection for migrations and the worker (RLS-exempt). |
| `APP_ROLE_PASSWORD` / `POSTGRES_PASSWORD` | App-role and owner passwords (must match their URL above). |
| `SESSION_SECRET` | Server-side session signing secret. |
| `MASTER_KEY` | Fernet key encrypting device **and SMTP** credentials at rest. |
| `MASTER_KEY_OLD_KEYS` | Comma-separated retired keys, decryption-only — used during key rotation. |
| `TZ` | Container/log timezone (IANA name). Data is UTC; report hours are UTC. |
| `SESSION_TTL_HOURS` / `SESSION_IDLE_MINUTES` | Absolute and idle session timeouts. |
| `CORS_ALLOW_ORIGINS` | Comma-separated allowed origins; empty = CORS disabled (same-origin). |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_WINDOW_SECONDS` | Login rate-limit / lockout. |
| `INGEST_EVERY_MINUTES`, `CONFIG_BACKUP_HOUR` | Worker cron cadences. |
| `SERVER_NAME` / `CERT_DIR` / `DOMAIN` / `ACME_EMAIL` / `FRONTEND_BIND` / `FRONTEND_HTTP_PORT` / `HTTP_PORT` / `HTTPS_PORT` | Frontend exposure & TLS (per the model chosen above). |

> **Report email delivery is configured in-app**, not via env: the superadmin sets the SMTP relay
> under *Admin → SMTP delivery*, and tenant admins set schedules + recipients per tenant/device. The
> hourly worker cron fires due schedules; failed sends retry every 10 min for up to 2 h. *(OAuth-based
> sending for Gmail/M365 is a planned follow-up.)*

## Security & multi-tenancy

- **Tenant isolation** — every tenant-scoped table carries a `tenant_id` and a fail-closed RLS policy
  (`ENABLE` + `FORCE`), including `report_schedule`. The API sets `app.current_tenant` per transaction
  and runs as `opngms_app`; cross-tenant isolation is covered by SQL-level and real-API tests. The
  global SMTP relay is **non-tenant** and reachable only by superadmin-gated endpoints.
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
- **Credentials** — argon2 password hashing; device **and SMTP** secrets encrypted with Fernet
  (`MASTER_KEY`), never returned by any API or written to logs (SMTP error text is sanitised before it
  reaches the audit log). Rotate with zero downtime via `MASTER_KEY_OLD_KEYS` + the re-key script.
- **Outbound safety** — SSRF guard on the connector (HTTPS only, no redirects, blocks
  loopback/link-local incl. cloud metadata, private ranges allowed, IP-pinned, sanitised errors), plus
  opt-in **TLS certificate fingerprint pinning** (verified before credentials are sent). Email is sent
  over STARTTLS/TLS with certificate validation on by default; report recipients are validated and
  capped, and mail headers are injection-hardened.
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
| **Report email delivery & scheduling** — per-tenant **and per-device** schedules (weekly/monthly/on-demand), each with a UTC hour and a recipient list; one superadmin **SMTP relay** (host/port/security/credentials, encrypted at rest) with a built-in test-send; per-tenant white-label **sender override**; manual **"send now"**; an hourly cron fires due schedules; send failures **retry every 10 min for up to 2 h** without re-rendering the PDF | ✅ Done |
| **Config management** — encrypted backup + **drift detection** (snapshot history + **on-demand live-vs-applied-template** drift with per-change badges) + firewall-aware editing UI + **live config push** (default-OFF master switch, now also a **runtime superadmin UI toggle**) | ✅ Done¹ |
| **OPNsense connector** — read/telemetry endpoints verified against real OPNsense 26.1.9; **(edition, version)-aware** endpoint matrix (Community / Business) | ✅ Done |
| **Device actions** — firmware update / multi-step major upgrade (reboot-tolerant) + plugin install/remove, now or scheduled, behind a per-device confirm; a "Firmware" UI tab + a WebGUI deep-link button; plugin install/remove verified live on real OPNsense 26.1.9² | ✅ Done |
| **Configuration templates (M1–M3)** — a global MSP **template library** (superadmin-managed) + per-tenant **override** + typed **apply** that reuses the config-push pipeline (preview → now/scheduled → snapshot), and **profiles** (M2): named, **ordered bundles of templates** applied to a device in one shot. A **kind-pluggable engine** ships five kinds: `firewall_alias`, the generic **`opnsense_setting`** (introspection-driven, value-controlled), **`suricata_ruleset`**, **`firewall_rule`** (Rules [new]/MVC; interface bound at apply time), and **`monit_test`**. Live-verified on real OPNsense 26.1.9³ | ✅ Done |
| **Login MFA (TOTP)** — TOTP second factor + one-time recovery codes; self-enroll + superadmin enforcement policy (off/all/privileged) with a fail-closed setup gate; two-step login (pending→full session); superadmin reset of a user's MFA + a host **break-glass CLI**; adversarially security-reviewed | ✅ Done |
| **Deployment** — production Dockerfiles + a base `docker-compose.prod.yml` (frontend HTTP, localhost-bound, safe-by-default) with override files for every TLS model (behind your proxy / built-in cert / automatic **Caddy** or **Traefik**); configurable container **timezone** | ✅ Done |
| **Hardening** — web hardening, TLS pinning, session lifecycle, `MASTER_KEY` rotation, CI security suite, branch protection | ✅ Done |
| **Log lake Phase 1** — opt-in `docker-compose.logs.yml` overlay; mTLS syslog-ng receiver (port 6514, CA-signed per-device client certs, CN/O → device_id/tenant_id); Suricata EVE JSON parsed inline; disk-buffered OpenSearch ingest (plain HTTP, internal-only, security disabled); daily indices | 🔧 Infra ready |
| **Log lake Phase 2** — tenant-scoped, backend-mediated log **search API** (`LOG_VIEW`: tenant admins + operators) with a mandatory path-injected `tenant_id` filter a Lucene query can't escape, guarded query_string, capped page size / time range / paging depth; an in-app **Logs** investigation page (time + device + Lucene filters, results table, raw-document modal) | ✅ Done |
| **Log lake Phase 3.1** — **provisioning UX**: a device-page "Log forwarding" tab to enable/disable forwarding (confirm-gated, `CONFIG_PUSH`), showing the client-cert expiry and a tenant-scoped **liveness** indicator ("last log received", best-effort OpenSearch lookup) | ✅ Done |
| **Log lake Phase 3.2** — **certificate lifecycle**: operator **Rotate** (re-issue + swap on the box, no log gap) and **soft Revoke** (deprovision + record the serial in an RLS revocation ledger, "Revoked" state) from the same card; box-gated transactions, audited. Hard CRL enforcement at the receiver is a tracked follow-up (3.2-bis) | ✅ Done |
| **Log lake Phase 3.3** — **scale**: unbounded **deep paging** for log search via OpenSearch **PIT + `search_after`** (stable across second-granularity timestamp ties, past the 10k window) with a "Load more" UI; plus a shipped **multi-node** OpenSearch config (3-node cluster compose + replicated index template) — HA verified at the staging bring-up | ✅ Done |
| **Log lake Phase 3.4** — **MSP fleet dashboard**: a superadmin-only cross-tenant **Log fleet** page (per-tenant forwarding status, ingest health with a "silent tenant" flag, **selectable 24h/7d/30d** volume, a **per-device silent drill-down**, **CSV/PDF export**, and **proactive silent-tenant alerting** — an hourly detector emails active superadmins once per silent episode + a dashboard banner). The console's first cross-tenant aggregate — forwarding counts via an RLS-scoped per-tenant loop, log volume/per-device stats via superadmin-only OpenSearch aggregations that return **aggregates only**, never raw cross-tenant log content | ✅ Done |

¹ Live configuration **push** to a device (firewall aliases + the templated kinds) is verified against
real OPNsense 26.1.9 and enabled behind a default-OFF `LIVE_PUSH_ENABLED` master switch (toggleable at
runtime by a superadmin), capturing a pre-apply config snapshot. An operator-triggered **targeted
Revert** reverses an applied change through the same pipeline (today the `firewall_alias` and
`opnsense_setting` kinds), and a cron **sweeper** re-enqueues orphaned/stuck scheduled actions;
full-config restore is intentionally not built (OPNsense exposes no restore API).

² Plugin install/remove was exercised end-to-end on the real 26.1.9 box (with guaranteed cleanup); firmware
update/upgrade are covered by mocked worker tests only (they reboot the device). True single-sign-on into the
WebGUI is a separate milestone — the button is currently a deep-link to the WebGUI login.

³ Configuration templates are a multi-milestone program: **M1** = the engine + the `firewall_alias` kind;
**M2** = profiles (ordered bundles, fan-out apply); **M3** = the kind-pluggable registries plus the generic
`opnsense_setting` (value-controlled, fleet-portable), `suricata_ruleset` (charset-guarded), `firewall_rule`
(interface is an apply-time binding so the template stays portable; idempotent upsert by `(description,
interface)`; **profile** apply threads the same `bindings` channel so a member rule can bind one interface
for the whole profile), and `monit_test` (condition + action, upserted by `name`). All merged & live-verified
on the real 26.1.9 box.

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

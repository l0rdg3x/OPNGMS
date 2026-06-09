# OPNGMS — OPNsense Global Management System

Console centralizzata multi-tenant per MSP che gestiscono e monitorano una flotta di firewall
**OPNsense**, ispirata al SonicWall/Dell Global Management System (GMS/SGMS). Inventario dei device,
monitoraggio di salute e rete, alerting, e — in roadmap — ingest di log/eventi, push di
configurazione e reportistica PDF per cliente.

> **Stato:** Fase 1 (Foundation & Inventory) e Fase 2 (Monitoring) **complete**. Vedi
> [Roadmap & stato](#roadmap--stato).

---

## Cos'è

OPNGMS dà a un MSP un'unica console per:

- **Inventariare** i firewall OPNsense dei clienti (onboarding, segreti API cifrati, test di
  raggiungibilità).
- **Monitorare** la flotta: polling periodico via API OPNsense → metriche di salute (CPU/mem/disco,
  uptime, firmware) e di rete (interfacce, gateway, VPN), stato up/down, alert.
- **Visualizzare** tutto in una dashboard per cliente: overview di flotta, salute per-device con
  grafici nel tempo, lista alert attivi/storici.

L'isolamento tra clienti è **strutturale**: schema condiviso + `tenant_id` + **Row-Level Security**
Postgres (fail-closed), con l'app che gira come ruolo non-superuser.

## Architettura

```
                ┌──────────────┐   cron 60s    ┌──────────────┐
                │ ARQ scheduler ├──────────────►│ Redis (broker)│
                └──────────────┘  enqueue       └──────┬───────┘
                                  poll_device(id)      │
                                                ┌──────▼────────┐  OpnsenseClient   ┌──────────┐
                                                │  ARQ worker(s) ├──────HTTPS───────►│ OPNsense │
                                                └──────┬────────┘  (SSRF-guarded)    └──────────┘
                                                       │ metrics / status / alerts (owner, bypass RLS)
   React + Mantine ──HTTP──► FastAPI ──RLS────► ┌──────▼─────────────────────────┐
   (dashboard)               (opngms_app)       │  TimescaleDB (Postgres + TS)    │
                                                │  metrics (hypertable), alerts,  │
                                                │  devices, tenants, users, ...   │
                                                └─────────────────────────────────┘
```

- **API** (FastAPI async): autenticazione a sessione + CSRF, RBAC a 4 ruoli, endpoint tenant-scoped.
  Si connette come ruolo non-superuser `opngms_app` → la RLS filtra per cliente.
- **Worker** (ARQ + Redis): cron che a cadenza fa l'enqueue di `poll_device` per ogni device; il
  worker raccoglie le metriche via `OpnsenseClient` (unico confine HTTP, con guardia SSRF), le
  scrive nell'hypertable TimescaleDB e valuta gli alert. Gira come owner DB (bypassa la RLS: è
  infrastruttura fidata, non user-facing).
- **Frontend** (Vite + React 19 + Mantine v9): shell multi-tenant, gestione device, dashboard di
  monitoraggio. Client API tipizzato generato dall'OpenAPI del backend.

## Stack tecnologico

| Area | Tecnologie |
|------|-----------|
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic, Pydantic v2 |
| Storage | TimescaleDB (Postgres 16 + estensione), hypertable per le metriche, RLS |
| Coda/worker | ARQ 0.28 + Redis |
| Sicurezza | argon2 (password), Fernet (segreti device), RLS Postgres, guardia SSRF |
| Frontend | Vite, React 19, TypeScript, Mantine v9 (+ Mantine Charts), TanStack Query, React Router |
| Test | pytest + pytest-asyncio + respx (backend); Vitest + Testing Library + MSW (frontend) |

## Struttura del repository

```
backend/         API FastAPI, worker ARQ, connettore OPNsense, modelli, migrazioni Alembic, test
frontend/        App React/Mantine (shell, pagine, monitoring/, client API tipizzato, test)
docs/superpowers/  spec di design e piani di implementazione per ogni fase/milestone
```

## Requisiti

- Docker + Docker Compose (per TimescaleDB e Redis)
- Python 3.12+ (con `venv`)
- Node.js 20+ e npm

## Avvio in sviluppo

### 1. Infrastruttura (DB + Redis)

```bash
cd backend
docker compose up -d db redis     # TimescaleDB + Redis
```

### 2. Backend (API)

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -e .

# variabili d'ambiente richieste (esempio sviluppo)
export DATABASE_URL=postgresql+asyncpg://opngms_app:opngms_app@localhost:5432/opngms
export ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms
export REDIS_URL=redis://localhost:6379
export SESSION_SECRET="<stringa-casuale>"
export MASTER_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"

alembic upgrade head              # applica le migrazioni (richiede l'owner ADMIN_DATABASE_URL)
uvicorn app.main:app --reload     # API su http://localhost:8000
```

Il primo superadmin si crea con l'endpoint one-time `POST /api/setup`.

### 3. Worker (poller)

```bash
cd backend
arq app.worker.WorkerSettings     # oppure: docker compose up worker
```

### 4. Frontend

```bash
cd frontend
npm install
npm run gen:api                   # (ri)genera i tipi API dall'OpenAPI del backend
npm run dev                       # dashboard su http://localhost:5173
```

## Test

```bash
# Backend (richiede un TimescaleDB di test raggiungibile)
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

## Sicurezza & multi-tenancy

- **Isolamento per cliente:** ogni tabella tenant-scoped ha `tenant_id` + policy RLS `tenant_isolation`
  (`ENABLE` + `FORCE`), fail-closed (`NULLIF` sul contesto assente). L'API imposta
  `app.current_tenant` per transazione e si connette come ruolo non-superuser `opngms_app`; il worker
  usa l'owner (superuser) per le scritture fleet-wide. Test di isolamento cross-tenant a livello SQL
  e via API reale.
- **Segreti dei device** cifrati con Fernet (`MASTER_KEY`); mai restituiti né loggati (write-only).
- **Guardia SSRF** sul connettore: solo https, niente userinfo, risoluzione DNS + blocco di
  loopback/link-local (incl. metadata cloud 169.254.169.254)/unspecified/multicast/reserved (le reti
  private RFC1918 sono ammesse), IP-pinning, niente redirect, errori sanificati.
- **Auth:** sessioni server-side + CSRF header sulle mutazioni; password con argon2; RBAC a 4 ruoli
  (superadmin + tenant_admin/operator/read_only).

## Roadmap & stato

| Fase | Contenuto | Stato |
|------|-----------|-------|
| **1 — Foundation & Inventory** | Scheletro, auth/RBAC, org-admin, device/segreti/onboarding, frontend shell | ✅ Completata |
| **SSRF hardening** | Guardia SSRF sul connettore OPNsense | ✅ Completata |
| **2 — Monitoring** | 2A poller core · 2B metriche rete + alerting · 2C API metriche/salute/alert + RLS · 2D dashboard frontend | ✅ Completata |
| **3 — Log/Event ingest** | Ingest syslog/eventi (Suricata, DNS/proxy) per i report | ⬜ Pianificata |
| **4 — Config push** | Push di configurazione verso i device | ⬜ Pianificata |
| **5 — Reporting PDF** | Report per cliente in stile SonicWall SGMS (attacchi, siti visitati, banda) | ⬜ Pianificata |
| **Deploy** | Dockerfile multi-stage + docker-compose di produzione per l'intero stack | ⬜ A fine progetto |

I documenti di design (spec) e i piani di implementazione di ogni milestone sono in
[`docs/superpowers/`](docs/superpowers/).

## Licenza

Vedi [LICENSE](LICENSE).

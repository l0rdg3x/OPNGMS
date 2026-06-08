# OPNGMS Backend

Backend del sistema di gestione centralizzata per firewall OPNsense — **Fase 1: Foundation & Inventario**.
Stack: FastAPI (async), SQLAlchemy 2.0 + asyncpg, Alembic, Postgres.

## Requisiti
- Python 3.12+
- Docker + Docker Compose (Postgres; i test di isolamento RLS richiedono un Postgres reale)

## Setup
La shell di default e' fish: NON serve `source .venv/bin/activate`, usa i binari del venv direttamente.

1. `python3 -m venv .venv`
2. `.venv/bin/pip install -e ".[dev]"`
3. Copia `.env.example` in `.env` e genera la `MASTER_KEY`:
   `.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. `make up` — avvia Postgres
5. `make createtestdb` — crea il database di test `opngms_test`
6. `make migrate` — applica le migrazioni come owner (schema + RLS + ruolo app `opngms_app`)

## Modello di connessione al DB (rilevante per la RLS)
La Row-Level Security isola i dati tra tenant. Poiche' i **superuser PostgreSQL bypassano sempre
la RLS**, i ruoli sono separati:
- **App (runtime):** si connette come `opngms_app`, ruolo **non-superuser** (`DATABASE_URL`). Solo
  cosi' la RLS vincola davvero le query dell'app.
- **Migrazioni / admin:** girano come owner `opngms` (`ADMIN_DATABASE_URL`), che crea schema,
  policy e il ruolo `opngms_app`.

Il ruolo `opngms_app` viene creato dalla migrazione `0003`. In produzione, cambia la sua password
(`ALTER ROLE opngms_app PASSWORD '...'`) e aggiorna di conseguenza `DATABASE_URL`.

## Test
I test di isolamento RLS richiedono il DB di test; senza `TEST_DATABASE_URL` vengono saltati.
```
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -v
```

## Avvio
```
.venv/bin/uvicorn app.main:app --reload
```
Poi: http://localhost:8000/healthz

## Migrazioni
- Applica: `make migrate` (usa `ADMIN_DATABASE_URL`, ovvero l'owner)
- Nuova revisione autogenerata: `make revision m="descrizione"`

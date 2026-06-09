# OPNGMS — Fase 2: Monitoraggio & Salute — Design Spec

- **Data:** 2026-06-09
- **Stato:** Approvato (design), in attesa di revisione finale dello spec
- **Fase:** 2 di 5 della roadmap OPNGMS
- **Dipende da:** Fase 1 (Foundation+Auth+Device+Frontend) in `main`

---

## 1. Contesto

La **Fase 2** dà a OPNGMS il monitoraggio di stato/salute della flotta OPNsense: un motore di
**polling** che, su cadenza, interroga ogni device via la sua REST API, raccoglie metriche, le
memorizza come serie temporali, aggiorna lo stato, genera alert, e le espone via API e dashboard.

I **log/eventi** (per i report della Fase 5) sono la Fase 3 — qui ci occupiamo dello *stato*
(polling), non delle *cronologie di eventi* (syslog).

## 2. Decisioni di design (brainstorming Fase 2)

| Tema | Decisione |
|------|-----------|
| Storage time-series | **TimescaleDB** (estensione Postgres): hypertable, compressione, continuous aggregates, retention native. Resta lo stesso DB/stack/migrazioni |
| Motore di polling | **ARQ + Redis** (coda di job async): cron → enqueue `poll_device` per device → worker concorrenti. Retry/backoff e osservabilità integrati |
| Scope metriche MVP | **Essenziale + rete**: up/down + last_seen, CPU/mem/disco, uptime, firmware+update; interfacce (stato+traffico), gateway (stato/RTT/loss), tunnel VPN (stato) |

Vincoli di piattaforma (Fase 1): Python/FastAPI, ~100-300 device, API diretta (pull), connector
`OpnsenseClient` (unico confine HTTP), app runtime come ruolo non-superuser `opngms_app` con RLS.

## 3. Architettura

```
                 ┌─────────────┐   cron 60s    ┌──────────────┐
                 │ ARQ scheduler├──────────────►│ Redis (broker)│
                 └─────────────┘  enqueue       └──────┬───────┘
                                  poll_device(id)      │ consume
                                                ┌──────▼───────┐  OpnsenseClient   ┌─────────┐
                                                │ ARQ worker(s) ├──────HTTPS───────►│ OPNsense │
                                                └──────┬───────┘  (privilegiato)    └─────────┘
                                                       │ write metrics / status / alerts
                                                ┌──────▼─────────────────────┐
   React dashboard ──HTTP──► FastAPI ──RLS────► │ TimescaleDB (Postgres+TS)   │
   (grafici)                 (read, opngms_app) │  metrics hypertable, alerts │
                                                └─────────────────────────────┘
```

- Il **poller** (processo `python -m app.worker`) è infrastruttura backend fidata: si connette con
  il ruolo **owner** (`ADMIN_DATABASE_URL`, bypassa la RLS) per leggere TUTTI i device e scrivere
  metriche/stato/alert.
- L'**API** legge metriche/alert come `opngms_app` (non-superuser) sotto **tenant-context** → la
  RLS filtra per cliente. Difesa-in-profondità identica a `devices`.

## 4. Modello dati

### 4.1 Hypertable `metrics` (TimescaleDB)
Narrow + etichettata, copre scalari e multi-dimensionali:
```
metrics(
  time        TIMESTAMPTZ NOT NULL,
  device_id   UUID NOT NULL,        -- (no FK: hypertable; integrità gestita dal poller)
  tenant_id   UUID NOT NULL,        -- denormalizzato: aggregazioni per cliente + RLS
  metric      TEXT NOT NULL,        -- es. 'cpu.load', 'mem.used_pct', 'iface.bytes_in', 'gateway.rtt_ms', 'vpn.up'
  label       TEXT NOT NULL DEFAULT '',  -- dimensione: '' per scalari, 'igb0'/'WAN_GW'/'wg0' per multi-dim
  value       DOUBLE PRECISION NOT NULL
)
```
- `create_hypertable('metrics', 'time')`; indice su `(tenant_id, device_id, metric, time DESC)`.
- **Continuous aggregate** `metrics_5m` (avg/max per metric+label, bucket 5 min) per le dashboard a
  lungo periodo. **Retention policy**: raw droppato dopo N giorni (config, default 30); il
  continuous aggregate ha retention più lunga.
- **RLS** sull'hypertable keyed su `tenant_id` (il poller owner bypassa; l'API filtra). Aggiunta a
  `TENANT_TABLES` (modulo `rls.py` esistente). `opngms_app` riceve SELECT (grant; verificare la
  propagazione ai chunk Timescale).

### 4.2 Tabella `alerts` (control-plane relazionale, non hypertable)
```
alerts(
  id          UUID PK,
  tenant_id   UUID NOT NULL,    -- RLS
  device_id   UUID NOT NULL FK devices ON DELETE CASCADE,
  type        TEXT NOT NULL,    -- 'device.down' | 'gateway.down' | ...
  label       TEXT,             -- es. nome gateway
  severity    TEXT NOT NULL DEFAULT 'warning',
  opened_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ,      -- NULL = attivo
  details     JSONB NOT NULL DEFAULT '{}'
)
```
- RLS keyed su `tenant_id`. Unico alert *attivo* per `(device_id, type, label)` (vincolo parziale
  su `resolved_at IS NULL`). Il poller apre/risolve gli alert sui cambi di stato.

### 4.3 Stato corrente sul `Device`
Lo stato *corrente* (up/down, last_seen, firmware_version) resta sui campi esistenti del `Device`,
aggiornati dal poller a ogni ciclo. Le metriche *correnti* (ultima CPU/mem/ecc.) si derivano
dall'hypertable (`last()` di Timescale) — niente tabella "snapshot" separata nell'MVP.

## 5. Motore di polling (ARQ + Redis)

- **`app/worker.py`**: `WorkerSettings` ARQ con functions + cron jobs + Redis settings.
- **Cron `enqueue_device_polls`** (ogni `POLL_INTERVAL_SECONDS`, default 60): lista tutti i device
  (owner connection), enqueue `poll_device(device_id)` per ciascuno.
- **`poll_device(device_id)`**: carica il device, decifra i segreti (`crypto`), costruisce
  `OpnsenseClient`, raccoglie le metriche, scrive su `metrics`, aggiorna `Device.status`/`last_seen`/
  `firmware_version`, valuta gli alert (transizioni di stato). Idempotente; ARQ **retry** con
  backoff su errori transitori.
- **Concorrenza/rate-limit**: `max_jobs` del worker ARQ bounda la concorrenza globale verso le API
  OPNsense.
- **Connessione DB del worker**: ruolo owner (`ADMIN_DATABASE_URL`) — vede tutti i device, scrive
  metriche/alert bypassando la RLS (è infrastruttura fidata, non user-facing).
- **docker-compose**: aggiunge servizi `redis` e `worker` (oltre a `db` ora TimescaleDB).

## 6. Estensioni del connector `OpnsenseClient`

Nuovi metodi async (un metodo per gruppo di metriche), che ritornano dict normalizzati; mantengono
il principio dell'**unico confine HTTP** e la normalizzazione errori esistente:
- `get_system_info()` → cpu/mem/disco/uptime
- `get_firmware_status()` (già esiste) → versione + update disponibili
- `get_interfaces()` → per interfaccia: stato, bytes in/out
- `get_gateways()` → per gateway: stato, RTT, loss
- `get_vpn_status()` → per tunnel: up/down

⚠️ **Endpoint OPNsense esatti DA VERIFICARE** contro un device reale (presumibilmente sotto
`/api/diagnostics/...`, `/api/routes/gateway/status`, `/api/wireguard/...`, ecc.). L'astrazione e i
test (mock respx) non cambiano; il mapping endpoint→metrica si conferma in implementazione.

## 7. API metriche/salute (FastAPI, tenant-scoped)

Sotto `/api/tenants/{tenant_id}/...`, gated da `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../devices/{device_id}/metrics?metric=&from=&to=` → serie temporale (dal continuous
  aggregate per range lunghi, raw per range brevi) + ultimo valore.
- `GET .../health` → riassunto per cliente: # device reachable/unverified/unreachable, # alert
  attivi.
- `GET .../alerts?active=true` → alert (attivi o storici) del cliente.

## 8. Dashboard frontend (React + Mantine)

- **Vista salute per-device**: grafici nel tempo (CPU/mem, traffico interfacce), stato gateway/VPN,
  ultimo aggiornamento. Libreria grafici (Mantine Charts / Recharts — scelta in fase di piano).
- **Overview per-cliente**: riepilogo salute flotta + lista alert attivi.

## 9. Scomposizione in milestone
1. **2A — Infra + storage + poller core**: TimescaleDB+Redis nel compose, migrazione (estensione +
   hypertable `metrics` + retention + RLS), setup ARQ, poller (cron→`poll_device`), connector
   `get_system_info`, raccolta **salute essenziale** (up/down, CPU/mem/disco, uptime, firmware) +
   update stato. *Definizione di fatto:* un device mockato viene "pollato", le metriche compaiono
   nell'hypertable, lo stato si aggiorna.
2. **2B — Metriche di rete + alerting**: connector interfacce/gateway/VPN + raccolta, motore alert
   (transizioni → tabella `alerts`, apri/risolvi).
3. **2C — API metriche/salute**: endpoint per-device (serie+ultimo), riassunto per-cliente, alert —
   tenant-scoped + RLS, con test di isolamento.
4. **2D — Dashboard frontend**: viste salute per-device (grafici) + overview per-cliente + alert.

Ogni milestone = spec→piano→esecuzione subagent-driven.

## 10. Testing
- **Poller**: `poll_device` testato con un `OpnsenseClient` mockato (respx) o un client fake
  iniettato; verifica scrittura metriche su un TimescaleDB di test + update stato + apertura alert.
  Il connector con respx (come Fase 1).
- **Storage**: i test girano su un TimescaleDB reale (l'estensione serve per create_hypertable); la
  conftest crea l'estensione + hypertable nel DB di test.
- **API**: integration test tenant-scoped + **isolamento metriche cross-tenant** (un cliente non
  vede le metriche di un altro), via RLS come per i device.
- **Alerting**: transizioni di stato (reachable→unreachable apre un alert; ritorno lo risolve).

## 11. Definizione di "fatto" (Fase 2)
- Il worker pollla la flotta su cadenza, con concorrenza bounded e retry.
- Le metriche essenziali+rete fluiscono nell'hypertable TimescaleDB; lo stato del device si aggiorna.
- Gli alert si aprono/risolvono sui cambi di stato.
- L'API espone metriche/salute/alert per cliente, isolate dalla RLS (test lo dimostrano).
- La dashboard mostra salute per-device e overview per-cliente.

## 12. Non-goals / rimandato
- Log/eventi e syslog ingest (Fase 3); config push (Fase 4); reporting PDF (Fase 5).
- Canali di notifica degli alert (email/webhook) — l'MVP genera/espone gli alert; l'invio è dopo.
- Scaling orizzontale multi-worker oltre il pool ARQ singola-istanza.
- Soglie di alert configurabili dall'utente (MVP: regole fisse device-down/gateway-down).

## 13. Domande aperte (non bloccanti)
- **Endpoint OPNsense** esatti per system/interfaces/gateways/VPN — da verificare contro un device
  reale; mockati fino ad allora.
- **Grant su hypertable Timescale** per `opngms_app` (propagazione ai chunk) — da verificare in 2A.
- **Cadenze multiple** (es. firmware/update ogni ora invece di 60s) — MVP: cadenza unica; si raffina
  con cron ARQ multipli se serve.
- **Libreria grafici** frontend (Mantine Charts vs Recharts) — decisa in fase di piano della 2D.

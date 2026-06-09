# OPNGMS — Fase 3: Ingest Log/Eventi — Design Spec

- **Data:** 2026-06-09
- **Stato:** Approvato (design); l'utente ha delegato le decisioni e autorizzato a procedere
- **Fase:** 3 di 5 della roadmap OPNGMS
- **Dipende da:** Fase 1 (Foundation+Auth+Device) e Fase 2 (Monitoring: poller, TimescaleDB, ARQ, RLS) in `main`
- **Abilita:** Fase 5 (Reporting PDF — Attacchi, Siti visitati)

---

## 1. Contesto

La **Fase 3** dà a OPNGMS l'**ingest di log/eventi** dalla flotta OPNsense: gli eventi di sicurezza
(alert IDS/IPS Suricata) e di navigazione (query DNS) vengono raccolti, normalizzati e memorizzati
come serie temporali, per alimentare i **report periodici** della Fase 5 (le sezioni "Attacks" e
"Web Activity / siti visitati").

A differenza della Fase 2 (stato/salute corrente via *polling*), qui raccogliamo **cronologie di
eventi discreti**. Il PDF e la visualizzazione ricca restano Fase 5; la Fase 3 si ferma a
ingest + storage + API di query (così i dati sono verificabili end-to-end).

## 2. Decisioni di design (brainstorming Fase 3)

| Tema | Decisione |
|------|-----------|
| Trasporto | **Pull via API** (il worker interroga l'API OPNsense), coerente con l'architettura outbound-only + SSRF già costruita; nessun listener inbound, riusa worker/connettore/RLS |
| Sorgenti MVP | **Entrambe**: Suricata IDS/IPS (alert/attacchi) e DNS (siti visitati) |
| Confine MVP | **Ingest + storage + API query** (PDF e frontend → Fase 5) |
| Cadenza | **Job di ingest separato** (default 300s), distinto dal poller metriche (60s) |
| Incrementalità | **Cursore per (device, source) + deduplica idempotente** sul pull |

## 3. Architettura

```
        ┌──────────────┐  cron 300s   ┌──────────────┐
        │ ARQ scheduler ├─────────────►│ Redis        │
        └──────────────┘ enqueue       └──────┬───────┘
                  ingest_device_events(id)     │ consume
                                        ┌──────▼─────────┐  OpnsenseClient   ┌──────────┐
                                        │  ARQ worker(s)  ├──────HTTPS───────►│ OPNsense │
                                        └──────┬─────────┘  (SSRF-guarded)    │ IDS, DNS │
                                               │ events (owner, bypass RLS)   └──────────┘
   FastAPI ──RLS──► opngms_app          ┌──────▼──────────────────────────┐
   GET .../events, .../events/top       │ TimescaleDB: events (hypertable),│
                                        │ ingest_cursors                   │
                                        └──────────────────────────────────┘
```

Il job di ingest è infrastruttura backend fidata: si connette come **owner** (`ADMIN_DATABASE_URL`,
bypassa la RLS) per leggere tutti i device e scrivere gli eventi. L'**API** legge come `opngms_app`
(non-superuser) sotto tenant-context → la RLS filtra per cliente, identico a metrics/alerts.

## 4. Modello dati

### 4.1 Hypertable `events` (TimescaleDB)
Stretta + JSONB per i campi sorgente-specifici (stesso principio di `metrics`):
```
events(
  time        TIMESTAMPTZ NOT NULL,   -- timestamp dell'evento (dalla sorgente)
  device_id   UUID NOT NULL,
  tenant_id   UUID NOT NULL,          -- denormalizzato: RLS + aggregazioni per cliente
  source      TEXT NOT NULL,          -- 'ids' | 'dns'
  category    TEXT NOT NULL DEFAULT '',-- es. 'alert' (ids), 'query' (dns)
  src_ip      TEXT NOT NULL DEFAULT '',-- initiator (client interno)
  dst_ip      TEXT NOT NULL DEFAULT '',
  name        TEXT NOT NULL DEFAULT '',-- signature (ids) / dominio (dns)
  severity    TEXT NOT NULL DEFAULT '',-- ids: 1..3 / low-high
  action      TEXT NOT NULL DEFAULT '',-- alert|drop (ids), allowed|blocked (dns)
  event_key   TEXT NOT NULL,          -- chiave naturale di deduplica (id sorgente o hash contenuto)
  attributes  JSONB NOT NULL DEFAULT '{}'  -- record normalizzato completo (flessibilità report)
)
```
- `create_hypertable('events', 'time')`; indice su `(tenant_id, device_id, source, time DESC)`.
- **Deduplica**: indice **unico** `(device_id, source, event_key, time)` (include `time`, richiesto da
  Timescale per gli unique sull'hypertable); insert con `ON CONFLICT DO NOTHING` → idempotente su poll
  sovrapposti, come la guardia degli alert (2B).
- **RLS** keyed su `tenant_id` (aggiunta a `TENANT_TABLES`; il worker owner bypassa, l'API filtra).
- **Compressione + retention** (default 90 giorni; gli eventi hanno volume > metriche).

### 4.2 Tabella `ingest_cursors` (stato interno del worker, NON hypertable, NON user-facing)
```
ingest_cursors(
  device_id   UUID NOT NULL,
  source      TEXT NOT NULL,
  last_time   TIMESTAMPTZ,    -- watermark: ultimo evento ingerito
  last_ref    TEXT,           -- riferimento opaco della sorgente (es. ultimo id/offset), nullable
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (device_id, source)
)
```
- Scritta/letta solo dal worker (owner). **Niente RLS** (non esposta via API; è stato interno).
  Integrità: il `device_id` riferisce un device esistente; alla cancellazione del device il cursore
  resta orfano ma innocuo (oppure FK CASCADE — deciso in fase di piano).

## 5. Pipeline di ingest

- **Cron `enqueue_event_ingests`** (ogni `INGEST_INTERVAL_SECONDS`, default 300): lista tutti i device
  (owner), enqueue `ingest_device_events(device_id)` per ciascuno.
- **`ingest_device_events(device_id)`**: carica device, decifra segreti, costruisce `OpnsenseClient`;
  per ogni **source** (`ids`, `dns`): legge il cursore `(device, source)`, chiama il metodo connettore
  con `since = last_time` (con piccolo overlap `δ` per non perdere eventi al bordo), normalizza,
  inserisce in `events` (`ON CONFLICT DO NOTHING`), aggiorna il cursore al `max(time)` ingerito.
  **Resiliente**: l'errore di una source (`OpnsenseError`) viene loggato e salta quella source, senza
  far fallire le altre né il job. Idempotente (cursore + dedup).
- **Concorrenza/rate-limit**: bounded dal `max_jobs` del worker ARQ (condiviso col poller metriche).

## 6. Estensioni del connettore `OpnsenseClient`

Nuovi metodi async (un metodo per source), che ritornano liste di dict normalizzati, mantenendo
l'**unico confine HTTP** + la guardia SSRF + la normalizzazione errori esistenti:
- `get_ids_alerts(since)` → alert Suricata: time, src_ip, dst_ip, signature, severity, action.
- `get_dns_events(since)` → query DNS: time, client_ip, domain, action (allowed/blocked).

Ogni dict include una `event_key` (id sorgente se disponibile, altrimenti hash del contenuto) e gli
`attributes` grezzi.

⚠️ **Endpoint OPNsense esatti DA VERIFICARE** contro un device reale (IDS presumibilmente
`/api/ids/service/queryAlerts` con paginazione; DNS più incerto — Unbound/Zenarmor). L'astrazione e i
test (mock respx) **non** cambiano; il mapping endpoint→campi si conferma in implementazione quando
sarà disponibile un device reale. **Suricata è la sorgente solida**; se l'API non espone i log DNS in
modo usabile, la **3B** resterà mockata fino al device reale (rischio segnalato, non bloccante per
storage/API).

## 7. API query (FastAPI, tenant-scoped + RLS)

Sotto `/api/tenants/{tenant_id}/...`, gated da `require_tenant(DEVICE_VIEW)` + tenant-context (RLS):
- `GET .../events?source=&device_id=&from=&to=&limit=` → lista eventi paginata (più recenti prima),
  con cap difensivo sul `limit` (come l'endpoint metriche 2C).
- `GET .../events/top?source=&field=src_ip|name&from=&to=&limit=` → aggregazione top-N per campo
  (prefigura le tabelle del report Fase 5: top initiators / signatures / siti). Conteggio per valore.

## 8. Scomposizione in milestone
1. **3A — Storage + framework ingest + Suricata**: hypertable `events` + RLS + migrazione; tabella
   `ingest_cursors`; cron + `ingest_device_events` con cursore/dedup; connettore `get_ids_alerts` +
   raccolta+normalizzazione IDS. *Fatto:* un device mockato viene "ingerito", gli alert IDS compaiono
   in `events`, il cursore avanza, i re-poll non duplicano.
2. **3B — Sorgente DNS**: connettore `get_dns_events` + raccolta+normalizzazione DNS nello stesso job.
3. **3C — API query**: endpoint lista + top-N, tenant-scoped + RLS, con test di isolamento cross-tenant.

Ogni milestone = spec→piano→esecuzione subagent-driven.

## 9. Testing
- **Ingest**: `ingest_device_events` testato con `OpnsenseClient` mockato (respx) o fake iniettato;
  verifica scrittura su `events`, avanzamento cursore, **idempotenza** (re-run non duplica), resilienza
  (errore di una source non blocca l'altra). Su TimescaleDB di test (conftest crea l'hypertable).
- **Connettore**: respx come Fase 1/2; mapping campi su payload IDS/DNS di esempio.
- **API**: integration tenant-scoped + **isolamento eventi cross-tenant** via RLS (come metrics/alerts).
- **Dedup**: due ingest con eventi sovrapposti → nessun duplicato (unique `ON CONFLICT`).

## 10. Definizione di "fatto" (Fase 3)
- Il worker ingerisce gli eventi (IDS + DNS) dalla flotta su cadenza, in modo incrementale e idempotente.
- Gli eventi normalizzati fluiscono nell'hypertable `events`, isolati per tenant dalla RLS.
- L'API espone lista + top-N degli eventi per cliente, con test di isolamento.
- I cursori avanzano; i poll sovrapposti non duplicano.

## 11. Non-goal / rimandato
- **Reporting PDF** (Fase 5) e **vista frontend eventi** (Fase 5).
- **Syslog push** (listener inbound): scelto il pull; il push è un'evoluzione futura.
- **Alert su eventi** (es. "troppi attacchi/ora"), correlazione/SIEM, GeoIP enrichment.
- **Sorgenti oltre IDS/DNS** (proxy Squid, flow/Zenarmor per Data Usage/Applications) — successive.

## 12. Domande aperte (non bloccanti)
- **Endpoint OPNsense** esatti per IDS/DNS (e formato dei payload) — da verificare contro un device
  reale; mockati fino ad allora. La 3B-DNS è la più a rischio (esposizione API dei log DNS incerta).
- **`event_key`/dedup**: id stabile fornito dalla sorgente vs hash del contenuto — deciso in 3A in base
  al payload IDS reale; default hash del contenuto normalizzato.
- **Retention/compressione** esatte (90g raw?) — affinabili; potrebbero divergere per source.
- **`ingest_cursors` FK/cleanup** alla cancellazione del device — FK CASCADE vs cursore orfano innocuo,
  deciso in piano 3A.

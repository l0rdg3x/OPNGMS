# OPNGMS — Fase 2 / Milestone 2D: Dashboard Frontend — Design Spec

- **Data:** 2026-06-09
- **Stato:** Approvato (design); l'utente ha delegato le decisioni e autorizzato a procedere
- **Fase:** 2 di 5 (Milestone 2D, ultima della Fase 2)
- **Dipende da:** Milestone D (shell frontend + auth + device) e Milestone 2C (API metriche/salute/alert) in `main`

---

## 1. Contesto

La **2D** chiude la Fase 2 dando alla console una **dashboard di monitoraggio**: consuma i tre
endpoint 2C (`GET .../devices/{id}/metrics`, `GET .../health`, `GET .../alerts`) e li presenta come
grafici nel tempo, riepiloghi di salute della flotta, e una lista alert gestibile. Costruisce sui
pattern già stabiliti da Milestone D: Vite + React 19 + Mantine v9 + React Router + TanStack Query,
client API tipizzato (`openapi-fetch` + `schema.d.ts` generato), test Vitest + RTL + MSW.

## 2. Decisioni di design (brainstorming 2D)

| Tema | Decisione |
|------|-----------|
| Libreria grafici | **Mantine Charts** (`@mantine/charts`, su Recharts): integrata col tema Mantine già in uso, API minimale (`LineChart`/`AreaChart`/`DonutChart`). Zero attrito con lo stack |
| Scope MVP | **Completo + gestione alert**: vista salute per-device, overview per-cliente, pagina alert con filtro attivi/storico |
| Metriche per-device | **Essenziale + rete**: CPU/mem/disco (serie) + traffico interfacce + stato gateway (RTT/loss/up) e VPN (up) + last_seen/firmware |
| Posizione overview | **`OverviewPage` come landing del tenant** (`/`): le card di salute sono la prima cosa che un MSP vuole vedere |

## 3. Architettura

### 3.1 Routing & navigazione (riorganizza `AppShell`)
Oggi `AppShell` ha `/` = `DevicesPage`. La 2D riorganizza le rotte (dentro `MantineAppShell.Main`)
e aggiunge le voci di navbar:

| Rotta | Pagina | Navbar |
|-------|--------|--------|
| `/` | `OverviewPage` (nuova) | **Overview** |
| `/devices` | `DevicesPage` (spostata da `/`) | **Device** |
| `/devices/:deviceId` | `DeviceDetailPage` (estesa) | — |
| `/alerts` | `AlertsPage` (nuova) | **Alert** |

I link interni esistenti verso i device (es. da `DevicesPage`) vanno aggiornati a `/devices/...`
dove necessario.

### 3.2 Data layer
- **`schema.d.ts` rigenerata** dall'OpenAPI del backend (include i 3 endpoint 2C). È un passo
  meccanico (`openapi-typescript`), prerequisito di tutto il resto.
- **Hook TanStack Query** sopra il client tipizzato `api`, uno per endpoint, tenant-scoped via
  `useTenant().activeId`:
  - `useTenantHealth()` → `GET /api/tenants/{tenant_id}/health`
  - `useAlerts({ active })` → `GET /api/tenants/{tenant_id}/alerts?active=`
  - `useDeviceMetrics(deviceId, metric, range)` → `GET .../devices/{device_id}/metrics?metric=&from=&to=&bucket=`
  - Query key namespacing per tenant (coerente con `["device", activeId, deviceId]` esistente).
- **Selettore time-range** (`1h` / `24h` / `7g`) → mappa a `from`/`to`/`bucket`:
  `1h`→bucket 60s, `24h`→300s, `7g`→3600s. Mantiene i punti sotto `MAX_POINTS` (5000) lato API e
  produce grafici lisci. Una util pura `rangeToParams(range, now)` calcola i parametri.

### 3.3 Pagine e componenti
- **`OverviewPage`** (`/`): card riepilogo da `/health` (device per stato + totale; n. alert
  attivi) + lista **alert attivi** da `/alerts?active=true`, con link al device.
- **`DeviceDetailPage`** (estesa): la sezione device esistente + **sezione salute** — card di stato
  (status, last_seen, firmware) + **grafici** con selettore time-range:
  - CPU/mem/disco → serie temporali (`cpu.pct`, `mem.pct`, `disk.pct`), + `uptime.seconds`
  - Traffico interfacce → `iface.bytes_in`/`iface.bytes_out` (multi-serie per label interfaccia),
    + `iface.up` (stato interfaccia)
  - Gateway → `gateway.rtt_ms`/`gateway.loss_pct`/`gateway.up` (per label gateway)
  - VPN → `vpn.up` (per label tunnel)

  *Nomi metrica confermati* contro `backend/app/services/monitoring.py` (poller 2A/2B).
- **`AlertsPage`** (`/alerts`): tabella alert con filtro **attivi/storico** (toggle `active=true|false`),
  colonne tipo/label/severità/aperto/risolto, ordinati per `opened_at` (l'API già ordina desc).
- **`MetricChart`** (componente riusabile): wrapper su `LineChart`/`AreaChart` di Mantine Charts;
  prende una serie `MetricPoint[]` (eventualmente multi-label → multi-serie), con label/unità.
  Trasforma i punti `{time,label,value}` nel formato dati di Mantine Charts.
- **Componenti di stato**: `HealthSummaryCards` (conteggi da `/health`), `StatusBadge`/`DeviceStatusCard`
  riusabili.

### 3.4 Data flow & gestione errori
- Loading/error gestiti da TanStack Query → skeleton Mantine durante il load, `Alert` Mantine in
  errore, **empty-state** quando non ci sono dati (es. device mai pollato → serie vuota: messaggio
  "nessun dato ancora").
- Tutto tenant-scoped: gli hook leggono `activeId` da `useTenant()`; il cambio tenant (via
  `TenantSwitcher`) invalida/rifetcha le query (le query key includono `activeId`).
- Le metriche/alert sono read-only nella 2D: nessuna mutazione (niente CSRF necessario per i GET).

## 4. Testing
- **MSW handlers** per `/metrics`, `/health`, `/alerts` aggiunti nei test via `server.use(...)`
  (il server è vuoto di default in `src/test/server.ts`).
- **Vitest + RTL** per pagina/componente:
  - `OverviewPage`: card mostrano i conteggi mock; lista alert attivi renderizzata; empty-state.
  - `DeviceDetailPage`: i grafici renderizzano dati i mock; selettore time-range cambia la query;
    empty-state su serie vuota.
  - `AlertsPage`: il toggle attivi/storico cambia la richiesta (`active=true|false`) e il contenuto.
  - `MetricChart`: mappa correttamente `MetricPoint[]` → dati Mantine Charts (test di trasformazione).
  - `rangeToParams`: util pura testata su tutti i range.
- Mantine Charts rende SVG/`ResponsiveContainer`: i test asseriscono presenza dei dati/strutture
  (serie, etichette, valori nel DOM), **non** pixel/dimensioni. Dove `ResponsiveContainer` ha
  problemi di dimensione in jsdom, mockare le dimensioni o usare i prop di width/height fissi nei
  test (pattern noto Recharts/jsdom).

## 5. Scomposizione in milestone (per il piano)
1. **Data layer**: rigenerazione `schema.d.ts` + hook (`useTenantHealth`/`useAlerts`/`useDeviceMetrics`)
   + util `rangeToParams` (con test) + install `@mantine/charts`.
2. **Componenti base**: `MetricChart` + `HealthSummaryCards`/card di stato (con test).
3. **`OverviewPage`** + riorganizzazione routing/navbar (`/`=Overview, `/devices`=Devices,
   `/alerts`=Alerts) (con test).
4. **`DeviceDetailPage` esteso**: sezione salute con grafici essenziale+rete + selettore time-range
   (con test).
5. **`AlertsPage`**: tabella + filtro attivi/storico (con test).

Ogni task = implementazione TDD + review (spec + qualità) subagent-driven.

## 6. Definizione di "fatto" (2D, e Fase 2)
- La navbar offre Overview / Device / Alert; il routing è riorganizzato senza rompere i link esistenti.
- L'Overview mostra il riepilogo salute flotta + gli alert attivi del cliente.
- La DeviceDetail mostra stato + grafici (CPU/mem/disco, traffico interfacce, gateway, VPN) con
  selettore time-range.
- La pagina Alert lista attivi e storico con filtro.
- Tutto tenant-scoped (cambio tenant rifetcha), con loading/error/empty-state.
- Suite frontend (Vitest) verde; `tsc`/lint puliti.
- **Con la 2D la Fase 2 è completa**: poller → storage → API → dashboard.

## 7. Non-goal / rimandato
- **Auto-refresh/polling lato UI** (live update dei grafici): l'MVP fetcha on-load/cambio-range;
  `refetchInterval` è un miglioramento successivo.
- **Export/print dei grafici**, range custom (date picker libero): MVP usa i 3 preset.
- **Continuous aggregate** lato API per range lunghi (debito 2C): il selettore `7g` usa bucket 3600s
  on-the-fly — accettabile; la CAGG materializzata resta per la Fase 5/ottimizzazione.
- **Bucket "naturali" enumerati lato API** (debito 2C): la UI passa `bucket` in secondi, sufficiente.
- **Gestione/azioni sugli alert** (ack/resolve manuale dalla UI): la 2D è read-only sugli alert;
  apertura/risoluzione resta del poller.

## 8. Domande aperte (non bloccanti)
- **Nomi metrica**: confermati contro `monitoring.py` (`cpu.pct`, `mem.pct`, `disk.pct`,
  `uptime.seconds`, `iface.bytes_in/out`, `iface.up`, `gateway.rtt_ms/loss_pct/up`, `vpn.up`). Gli
  endpoint OPNsense reali sono però ancora da validare (l'utente li fornirà): i *valori* potrebbero
  rifinirsi, ma i *nomi* delle metriche restano il contratto stabile lato dashboard.
- **Unità/formattazione** (bytes→MB/s, % , ms): scelte di presentazione decise in fase di piano.

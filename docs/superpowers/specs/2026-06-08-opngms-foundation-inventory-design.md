# OPNGMS — Fase 1: Foundation & Inventario — Design Spec

- **Data:** 2026-06-08
- **Stato:** Approvato (design), in attesa di revisione finale dello spec
- **Autore:** l0rdg3x (brainstorming con Claude)
- **Fase:** 1 di 5 della roadmap OPNGMS

---

## 1. Contesto

**OPNGMS** (OPNsense Global Management System) è una console centralizzata per gestire e
monitorare una flotta di firewall **OPNsense** da un unico pannello, sul modello del
**SonicWall Global Management System (GMS)**.

**Audience:** MSP (Managed Service Provider) che gestiscono gli OPNsense di **clienti
diversi**, i cui dati devono restare **isolati** tra loro. Multi-tenancy, RBAC e audit log
fanno quindi parte del nucleo, non sono opzionali.

Questo documento specifica **solo la Fase 1 (Foundation & Inventario)**: la spina dorsale
su cui si appoggiano tutte le fasi successive.

## 2. Decisioni di piattaforma (valide per tutto OPNGMS)

Decise durante il brainstorming e vincolanti per le fasi successive:

| Tema | Decisione |
|------|-----------|
| Audience | MSP multi-cliente, isolamento tra tenant obbligatorio |
| Connettività ai device | **API diretta (pull)**: OPNGMS chiama la REST API di OPNsense. La raggiungibilità dei firewall è una **precondizione di deployment** (IP pubblico/mgmt, port-forward, o VPN gestita dall'MSP), non un problema risolto da OPNGMS |
| Scala target | Media: ~decine di clienti, **~100-300 device** complessivi |
| Stack | **Backend Python/FastAPI** (async) + **frontend React/TypeScript**; Postgres come DB |
| Scope monitoraggio | Salute/stato essenziali **+ ingest di log/eventi** finalizzato a futuri report |
| Scope config | Backup/drift + **push device-by-device** (alias + regole firewall) |
| Obiettivo reporting | Report PDF settimanali/mensili per cliente in stile SonicWall (attacchi, siti visitati, banda) — **fase successiva**, ma lo storage eventi va modellato fin da subito per renderlo possibile |

## 3. Roadmap (le 5 fasi)

OPNGMS è una piattaforma multi-sottosistema: viene costruita in **fasi**, ognuna con il
proprio ciclo spec → piano → implementazione.

1. **Foundation & Inventario** *(questo spec)* — modello dati multi-tenant, auth + RBAC +
   audit, onboarding device, connector OPNsense, scheletro FastAPI + shell React.
2. **Monitoraggio & Salute** — polling concorrente, metriche (up/down, CPU/mem/disco,
   firmware/update, interfacce+traffico, gateway, VPN), storage time-series, dashboard,
   alerting base.
3. **Ingest Log/Eventi** — ricevitore syslog, parser (firewall, Suricata, DNS/proxy),
   storage eventi report-ready, ricerca log base, scelta fonte "siti visitati".
4. **Gestione Config** — backup `config.xml` versionato + drift detection + restore;
   editing+push di alias e regole firewall device-by-device con diff/preview/apply.
5. **Reporting** — aggregazioni su metriche+eventi, template report, generazione PDF,
   scheduling settimanale/mensile + invio email per cliente.

## 4. Scope della Fase 1

### In scope
- Modello dati multi-tenant con isolamento a doppio livello (applicativo + RLS Postgres).
- AuthN a sessione, RBAC a 4 ruoli, audit log append-only.
- Onboarding device OPNsense con test raggiungibilità/credenziali e segreti cifrati write-only.
- `OpnsenseClient`: l'unica astrazione che parla HTTP con i firewall.
- Scheletro backend (FastAPI a strati) + frontend (React shell con tenant switcher e CRUD inventario).
- Suite di test centrata sulle invarianti critiche (isolamento, RBAC, segreti, connector).

### Fuori scope (fasi successive o esplicitamente rimandato)
- Qualsiasi polling di metriche o dashboard di monitoraggio (Fase 2).
- Ingest log/eventi e reporting (Fasi 3 e 5).
- Push o backup di configurazione (Fase 4).
- SSO/OIDC, 2FA TOTP, notifiche.
- Entità "Site/Location" dedicata (per ora bastano label `site` + `tags` sul device).

## 5. Architettura (vista d'insieme)

```
React SPA  ──HTTPS──>  FastAPI (async)
                         ├─ api/        router per risorsa
                         ├─ services/   logica + scoping per tenant
                         ├─ repositories/  accesso DB (WHERE tenant_id)
                         ├─ connectors/opnsense/  OpnsenseClient ──HTTPS basic-auth──> OPNsense REST API
                         └─ core/       config, crypto, auth, audit, RLS
                         │
                       Postgres (schema condiviso, tenant_id + RLS)
```

Principio guida: ogni unità ha **una responsabilità chiara** e comunica tramite interfacce
ben definite. Il `connectors/opnsense` isola il confine esterno; i `repositories`
isolano l'accesso DB e l'enforcement del tenant.

## 6. Modello dati

Tutte le entità tenant-scoped portano `tenant_id`. Campi `🔒` sono cifrati at-rest.

- **Tenant** — `id, name, slug, status, note, created_at`. Confine di isolamento.
- **User** (staff MSP) — `id, email, name, password_hash, is_superadmin, status, last_login, created_at`.
  Gli utenti appartengono all'organizzazione MSP; l'accesso ai clienti passa dalle membership.
- **Membership** (User ↔ Tenant + ruolo) — `id, user_id, tenant_id, role`. Assegna un ruolo
  a un utente *dentro* un cliente. I `SuperAdmin` bypassano le membership.
- **Device** (firewall OPNsense) — `id, tenant_id, name, base_url, api_key🔒, api_secret🔒,
  verify_tls, tls_fingerprint, site, tags, status, last_seen, firmware_version, created_at`.
  Appartiene a **esattamente un** tenant.
- **AuditLog** — `id, ts, actor_user_id, tenant_id(nullable), action, target_type, target_id,
  ip, details(json)`. Append-only.

`status` del Device ∈ `{reachable, unverified, unreachable}`.

## 7. Multi-tenancy & isolamento

Modello scelto: **schema condiviso + `tenant_id`**, con isolamento a **doppio livello**
(difesa in profondità):

1. **Strato applicativo (obbligatorio).** Un *request context* (middleware) risolve
   l'utente autenticato e il **tenant attivo** (dal path, es.
   `/api/tenants/{tenant_id}/devices`) e **autorizza l'accesso prima di ogni handler**
   (membership valida oppure `is_superadmin`). Tutte le query tenant-scoped passano da un
   repository che **inietta sempre** `WHERE tenant_id = :ctx`. Non sono ammesse query
   ad-hoc che possano "dimenticare" il filtro.

2. **Postgres Row-Level Security (RLS).** Policy a livello DB basate su una variabile di
   sessione `app.current_tenant`, impostata a ogni richiesta. Anche se un bug applicativo
   dimenticasse il filtro, il DB blocca comunque il leak cross-tenant. Le policy RLS sono
   create da migrazioni Alembic versionate.

I `SuperAdmin` operano su tutti i tenant; il context imposta `app.current_tenant` al tenant
attivo selezionato anche per loro (evitando query globali accidentali).

## 8. Autenticazione & sessioni

- Sessioni **server-side** con cookie `httpOnly` + `secure` + `SameSite`.
- Login email + password; hashing **argon2**.
- Endpoint: `login`, `logout`, `me`.
- Solo account locali nell'MVP. SSO/OIDC e 2FA TOTP sono rimandati (segnalati come estensioni).

## 9. RBAC — ruoli e matrice permessi

Quattro ruoli. `SuperAdmin` è un **flag a livello utente** (staff MSP); gli altri tre sono
assegnati **per-tenant** tramite Membership.

- **SuperAdmin** — accesso a tutti i clienti + amministrazione org (CRUD tenant, CRUD utenti).
- **TenantAdmin** — gestisce tutto dentro un cliente, incluse le membership di quel cliente.
- **Operator** — azioni operative su device dentro un cliente; niente gestione utenti/membership.
- **ReadOnly** — sola lettura dentro un cliente.

| Azione | SuperAdmin | TenantAdmin | Operator | ReadOnly |
|--------|:---:|:---:|:---:|:---:|
| CRUD tenant (org) | ✅ | ❌ | ❌ | ❌ |
| CRUD utenti (org, globale) | ✅ | ❌ | ❌ | ❌ |
| Gestione membership (nel tenant) | ✅ | ✅ | ❌ | ❌ |
| Vedere device | ✅ | ✅ | ✅ | ✅ |
| Creare/modificare/eliminare device | ✅ | ✅ | ✅ | ❌ |
| Test connessione device | ✅ | ✅ | ✅ | ❌ |
| Rotate segreto device | ✅ | ✅ | ✅ | ❌ |
| Vedere audit log (scoped al tenant) | ✅ | ✅ | ✅ | ✅ |

I permessi sono applicati da un *policy layer* (dependency FastAPI) che valuta
`(ruolo, azione)` su questa matrice esplicita.

> **Nota:** la creazione di utenti è riservata al `SuperAdmin`. Il `TenantAdmin`
> "gestisce le membership" nel senso che **assegna ruoli a utenti già esistenti** (e
> rimuove membership) dentro il proprio cliente, ma non crea nuovi account.

## 10. Audit log

Ogni azione che cambia stato scrive una riga **append-only**: login/logout, CRUD device,
`test`/`reveal`/`rotate` segreti, CRUD utenti/tenant/membership. Ogni riga registra attore,
tenant, tipo+id del target, IP e un riassunto dei cambiamenti. In UI verrà esposto in una
fase successiva; in Fase 1 viene scritto e coperto da test.

## 11. Onboarding device

1. Dentro un cliente, l'utente crea un device: `base_url`, **API key**, **API secret**,
   opzioni TLS (verifica CA / fingerprint pinning).
2. Il backend esegue un **test raggiungibilità + credenziali**: una GET autenticata leggera
   verso l'API OPNsense (stato firmware/sistema).
3. **Successo** → salva, mette in cache `firmware_version`, marca `status = reachable`.
   **Fallimento** → salva comunque con `status = unverified` e mostra l'errore **preciso**
   (DNS irrisolto / TLS non valido / 401 credenziali / timeout), così l'utente può correggere
   senza ricreare il device.

Salvare anche i device non verificati è una scelta deliberata: permette di sistemare
credenziali/rete in un secondo momento senza perdere l'inserimento.

## 12. Gestione segreti

- `api_key` / `api_secret` cifrati at-rest con **cifratura autenticata** (libsodium
  secretbox / Fernet), **chiave master da variabile d'ambiente** (in futuro KMS/Vault).
- I segreti sono **write-only verso il frontend**: dopo la creazione non tornano mai al
  client; l'UI mostra valori mascherati + azione **"rotate"**.
- La decifratura avviene **solo server-side**, nell'istante della chiamata al device.
- Ogni `reveal`/`rotate`/`test` finisce nell'audit log.

## 13. Connector OPNsense

Un'unica astrazione **`OpnsenseClient`** incapsula:
- `base_url`, auth **HTTP Basic** (`api_key` come username, `api_secret` come password);
- verifica TLS (con fingerprint pinning opzionale), timeout, retry/backoff;
- **normalizzazione errori**: `AuthError` (401) / `ReachabilityError` (DNS/TLS/timeout) /
  `ApiError(status)` (4xx/5xx) / `ParseError`.

**È l'unico punto** che parla HTTP con OPNsense: ogni altro modulo passa di qui. Questo
isola il confine esterno (facile da mockare nei test) e lo rende estendibile nelle fasi 2-4
senza toccare i consumatori.

Fase 1 usa solo `test_connection()`, `get_system_info()`, `get_firmware_status()`. Il client
nasce predisposto per ricevere una **sessione HTTP condivisa** e **limiti di concorrenza
per-device** che serviranno al polling (Fase 2).

> **Da verificare in implementazione:** gli endpoint esatti per raggiungibilità/versione
> (presumibilmente `core/firmware/status` e `core/system/...`), confermati contro un
> OPNsense reale o la doc API. L'astrazione non cambia.

## 14. Struttura backend

```
backend/
  app/
    api/            # router per risorsa (auth, tenants, users, devices, audit)
    services/       # logica di business + scoping per tenant
    repositories/   # accesso DB, enforcement WHERE tenant_id
    models/         # SQLAlchemy (async) + schema
    connectors/
      opnsense/     # OpnsenseClient (unico confine HTTP esterno)
    core/           # config, crypto/segreti, auth/sessioni, audit, RLS deps
    main.py
  migrations/       # Alembic (incl. policy RLS)
  tests/
```

Runtime: SQLAlchemy **async** + Postgres, **Alembic** per migrazioni (incluse le policy
RLS), **pydantic-settings** per la config, **argon2** per le password.

## 15. Scheletro frontend

- React + TypeScript con **Vite**.
- Client API **tipizzato, generato da OpenAPI** (niente tipi scritti a mano fuori sync).
- Auth context + **shell dell'app**:
  - pagina **login**;
  - top bar con **tenant switcher** (per utenti multi-cliente / SuperAdmin);
  - nav laterale;
  - **lista Device** per cliente attivo (add / edit / test-connection / rotate);
  - device detail stub; sezione admin (tenant + utenti) stub.
- La Fase 1 è **scheletro + CRUD inventario**, non dashboard.
- La libreria di componenti (Mantine vs shadcn/ui) viene scelta in fase di **piano**: non è
  una decisione architetturale e non blocca il design.

## 16. Deployment & configurazione

- `docker-compose` per lo sviluppo locale: `api` + Postgres + frontend.
- Target di deploy: **single-instance** (adeguato alla scala media).
- `.env` per: chiave master di cifratura, DB URL, secret di sessione.

## 17. Strategia di test

TDD sulle invarianti critiche:

- **Isolamento tenant (priorità massima):** un utente/richiesta nel contesto del cliente A
  **non può** leggere/scrivere dati del cliente B — testato a livello service e via API
  integration. Test specifico che **la RLS blocca il leak** anche se il filtro applicativo
  viene bypassato.
- **RBAC:** la matrice `(ruolo × azione)` diventa una tabella di casi di test.
- **Gestione segreti:** segreti cifrati at-rest, **mai serializzati** nelle risposte API,
  `rotate`/`reveal` registrati in audit.
- **Connector:** mock del confine HTTP (es. `respx`) → verifica header di auth, opzione TLS,
  normalizzazione errori (401 → `AuthError`, timeout → `ReachabilityError`, …). Nessun device
  reale necessario.
- **Onboarding:** integration test del flusso create-device con `test_connection` mockato sui
  rami successo/fallimento.
- Tooling: **pytest** + httpx test client, factory fixtures, fake OPNsense server.

## 18. Definizione di "fatto" (Fase 1)

- Un SuperAdmin può creare tenant e utenti, e assegnare membership con ruolo.
- Un utente può autenticarsi, selezionare un tenant a cui ha accesso, e fare CRUD dei device
  di quel tenant dalla UI.
- L'onboarding di un device esegue il test connessione e riporta esito/errore preciso.
- I segreti dei device sono cifrati at-rest e non escono mai verso il frontend.
- L'isolamento cross-tenant è garantito a livello applicativo **e** dalla RLS, con test che
  lo dimostrano.
- La matrice RBAC è applicata e coperta da test.
- Le azioni che cambiano stato sono registrate nell'audit log.

## 19. Domande aperte (per fasi successive, non bloccanti)

- **Fonte dati "siti visitati"** (Fase 3/5): Suricata per gli attacchi è chiaro; per i siti
  visitati le opzioni sono log DNS Unbound (dominio, leggero), Squid proxy (URL completi),
  o Zenarmor/Sensei (visibilità app/web/utente, la più vicina a SonicWall ma plugin pesante).
  Da decidere nella fase di ingest/reporting.
- **Libreria UI** (Mantine vs shadcn/ui): da decidere in fase di piano.

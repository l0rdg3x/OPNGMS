# OPNGMS — Fase 3 / Milestone 3B: Sorgente DNS — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggiungere la sorgente **DNS** (query Unbound → "siti visitati") all'ingest eventi, riusando il framework 3A (hypertable `events`, cursore per `(device, source)`, dedup, worker).

**Architecture:** La 3A ha reso l'ingest generico sul `source`. La 3B aggiunge solo: un metodo connettore `get_dns_events(since)` che normalizza le query DNS, e l'attivazione della source `"dns"` nel servizio `ingest_events` (lista `SOURCES` + dispatch `_fetch`). Storage, cursore, dedup, RLS, cron e job restano invariati.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, TimescaleDB, ARQ, pytest + respx.

---

## Contesto per l'implementatore (leggere prima di iniziare)

Codebase backend in `/home/l0rdg3x/coding/OPNGMS/backend`. La 3A è già in `main`.

- **Connettore** (`app/connectors/opnsense/client.py`): `get_ids_alerts(since)` (righe ~167-201) è il modello da replicare per `get_dns_events`. Usa `self._get(path)` (unico confine HTTP + SSRF), `self._parse_ts(...)` (ritorna sempre `datetime` tz-aware), `self._event_key(ts, *parts)` (hash discriminante quando manca un id sorgente). `datetime`/`timezone`/`hashlib` sono già importati.
- **Servizio ingest** (`app/services/ingest.py`):
  - `SOURCES = ["ids"]` → diventa `["ids", "dns"]`.
  - `_fetch(client, source, since)` fa il dispatch per source (oggi solo `ids`); aggiungi il ramo `dns`.
  - `_normalize(device, source, r)` è **già generico**: legge `time, category, src_ip, dst_ip, name, severity, action, event_key, attributes` dal dict del connettore. NON va modificato (il dict DNS deve avere queste chiavi).
  - `ingest_events` è resiliente per-source (`except OpnsenseError: continue`): un errore della sorgente DNS non blocca IDS e viceversa.
- **Modello eventi** (`app/models/event.py`): `Event` con PK dedup `(time, device_id, source, event_key)`. Per DNS: `source="dns"`, `category="query"`, `src_ip=client_ip`, `name=domain`, `action=allowed|blocked`.
- **Test ingest** (`tests/test_ingest.py`): contiene un `FakeClient` con SOLO `get_ids_alerts`. ⚠️ **Aggiungendo `"dns"` a `SOURCES`, `ingest_events` chiamerà `client.get_dns_events` anche nei test esistenti** → senza aggiornare `FakeClient` si avrebbe `AttributeError` (NON un `OpnsenseError`, quindi non catturato) e i 3 test 3A si romperebbero. Il `FakeClient` e i suoi call-site VANNO aggiornati (Task 2).
- **Test connettore**: `tests/test_connector_ids.py` è il modello per `tests/test_connector_dns.py` (respx).

**Comando test** (dir `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Suite attuale: **138 test verdi**.

⚠️ **Endpoint OPNsense DNS DA VERIFICARE — la sorgente più incerta** (debito 3A): l'esposizione API dei log DNS di OPNsense (Unbound) non è confermata. `get_dns_events` è scritto contro un payload *plausibile* e testato con respx; se sul device reale non esiste un endpoint usabile, la raccolta DNS resterà mockata fino ad allora. **NON è un blocco** per storage/dedup/API: l'astrazione regge e la dedup ON CONFLICT è la rete di sicurezza. Niente schema/migrazioni nuove in 3B.

---

## File Structure

| File | Responsabilità | Azione |
|------|----------------|--------|
| `app/connectors/opnsense/client.py` | `get_dns_events(since)` | Modify |
| `tests/test_connector_dns.py` | respx per `get_dns_events` | Create |
| `app/services/ingest.py` | `"dns"` in `SOURCES` + dispatch `_fetch` | Modify |
| `tests/test_ingest.py` | `FakeClient` multi-source + test DNS/both/resilienza | Modify |

---

## Task 1: Connettore `get_dns_events`

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_dns.py`

- [ ] **Step 1: Scrivere il test respx (fallisce)**

Crea `tests/test_connector_dns.py` (mirror di `test_connector_ids.py`). Payload DNS *plausibile* (query Unbound):
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_dns_events_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "client": "10.0.0.20",
                "domain": "example.com",
                "action": "allowed",
                "query_id": "q1",
            }
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.20"
    assert e["name"] == "example.com"       # dominio = "sito visitato"
    assert e["action"] == "allowed"
    assert e["category"] == "query"
    assert e["dst_ip"] == ""
    assert e["severity"] == ""
    assert e["event_key"]                    # id sorgente o hash
    assert e["time"].tzinfo is not None      # tz-aware


@respx.mock
async def test_get_dns_events_key_variants_and_empty():
    # varianti di chiave + fallback hash + payload vuoto
    payload = {
        "queries": [
            {"time": "2026-06-09T13:00:00Z", "client_ip": "10.0.0.21", "query": "blocked.test", "action": "blocked"}
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events()
    assert out[0]["src_ip"] == "10.0.0.21"
    assert out[0]["name"] == "blocked.test"
    assert out[0]["action"] == "blocked"
    assert out[0]["event_key"]  # hash del contenuto (nessun id)

    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={})
    )
    assert await client.get_dns_events() == []
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `... pytest tests/test_connector_dns.py -v` → FAIL (`get_dns_events` inesistente).

- [ ] **Step 3: Implementare `get_dns_events`**

In `app/connectors/opnsense/client.py`, aggiungi dopo `get_ids_alerts` (e prima di `_parse_ts`):
```python
    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        """Query DNS (Unbound) normalizzate → "siti visitati".

        NOTA: endpoint `unbound/diagnostics/queries` e formato del payload DA VERIFICARE
        su un OPNsense reale — è la sorgente più incerta (vedi debito 3A). Difensivo verso
        varianti di chiave. `since` è un hint: filtro fine e dedup avvengono a valle.
        """
        data = await self._get("unbound/diagnostics/queries")
        out: list[dict] = []
        for r in data.get("rows", data.get("queries", [])):
            ts = self._parse_ts(r.get("timestamp", r.get("time")))
            client_ip = r.get("client") or r.get("client_ip") or ""
            domain = r.get("domain") or r.get("query") or r.get("name") or ""
            action = r.get("action", "")  # allowed | blocked
            # event_key discriminante: id stabile se presente, altrimenti hash del contenuto.
            key = r.get("query_id") or r.get("id") or r.get("_id") or self._event_key(
                ts, client_ip, domain, action
            )
            out.append({
                "time": ts,
                "category": "query",
                "src_ip": client_ip,
                "dst_ip": "",
                "name": domain,
                "severity": "",
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out
```

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `... pytest tests/test_connector_dns.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_dns.py
git commit -m "feat(backend): connettore get_dns_events (normalizzazione query DNS Unbound)"
```

---

## Task 2: Attivare la source `dns` nell'ingest

**Files:**
- Modify: `app/services/ingest.py`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: Aggiornare `FakeClient` + scrivere i test DNS (falliscono)**

In `tests/test_ingest.py`, **sostituisci** il `FakeClient` esistente con una versione multi-source (mantiene la compatibilità col primo argomento posizionale `alerts`):
```python
class FakeClient:
    def __init__(self, alerts=None, dns=None, fail_ids=False, fail_dns=False):
        self._alerts = alerts or []
        self._dns = dns or []
        self._fail_ids = fail_ids
        self._fail_dns = fail_dns

    async def get_ids_alerts(self, since=None):
        if self._fail_ids:
            raise ReachabilityError("boom")
        return self._alerts

    async def get_dns_events(self, since=None):
        if self._fail_dns:
            raise ReachabilityError("boom")
        return self._dns
```
**Aggiorna il call-site esistente** in `test_ingest_resilient_to_source_error`: `FakeClient([], fail=True)` → `FakeClient(fail_ids=True)` (il vecchio kwarg `fail` non esiste più). Gli altri call-site (`FakeClient([_alert(...)])`) restano validi.

Aggiungi un helper `_dns` e i nuovi test in fondo al file:
```python
def _dns(ts, key, client="10.0.0.20", domain="example.com", action="allowed"):
    return {
        "time": ts, "category": "query", "src_ip": client, "dst_ip": "",
        "name": domain, "severity": "", "action": action, "event_key": key, "attributes": {},
    }


async def test_ingest_dns_writes_events(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        src = (await s.execute(text("SELECT source FROM events WHERE source='dns'"))).scalars().all()
    assert src == ["dns"]


async def test_ingest_both_sources_in_one_run(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 2  # 1 ids + 1 dns
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events ORDER BY source"))).scalars().all()
    assert srcs == ["dns", "ids"]


async def test_ingest_dns_fails_ids_succeeds(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        # DNS solleva, IDS riesce: la resilienza per-source garantisce che IDS venga comunque ingerito
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], fail_dns=True), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events"))).scalars().all()
    assert srcs == ["ids"]
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `... pytest tests/test_ingest.py -v`
Expected: i nuovi test DNS FALLISCONO (la source `dns` non è in `SOURCES`, quindi nessun evento dns scritto). I 3 test 3A esistenti devono comunque PASSARE (FakeClient ora ha `get_dns_events` che ritorna `[]` di default → la nuova iterazione `dns` non rompe nulla; il test di resilienza usa `fail_ids=True`).

- [ ] **Step 3: Attivare la source `dns`**

In `app/services/ingest.py`:
```python
SOURCES = ["ids", "dns"]
```
e in `_fetch`, aggiungi il ramo `dns`:
```python
async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    if source == "dns":
        return await client.get_dns_events(since)
    raise ValueError(f"source sconosciuta: {source}")
```

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `... pytest tests/test_ingest.py -v` → tutti PASS (3 esistenti + 3 nuovi). Poi l'INTERA suite verde.

- [ ] **Step 5: Commit**
```bash
git add app/services/ingest.py tests/test_ingest.py
git commit -m "feat(backend): attiva la source DNS nell'ingest (SOURCES + dispatch _fetch)"
```

---

## Task 3: Debito tecnico

- [ ] **Step 1: Registrare il debito 3B**

Append a questo piano:
```markdown
## Debito tecnico (3B)

- **Endpoint DNS DA VERIFICARE (sorgente più incerta)**: `unbound/diagnostics/queries` e il payload
  sono plausibili ma non confermati. Se OPNsense non espone i log DNS via API in modo usabile, valutare
  una sorgente alternativa (Zenarmor, export periodico) o il passaggio a syslog push per il DNS.
- **`since` non onorato anche per DNS** (come IDS): filtro client-side + dedup; rifinire col device reale.
- **Niente `dst_ip`/resolver per DNS**: `dst_ip=""`. Se servisse il resolver upstream per i report,
  mapparlo dagli attributes.
- **Stesso evento DNS allo stesso istante** (stesso client+dominio+action, nessun id): collassa per dedup
  — accettabile, ma per i conteggi "hits per sito" potrebbe sottostimare query identiche ravvicinate.
  Valutare un contatore o un id sorgente quando disponibile.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase3-milestone3B-dns.md
git commit -m "docs: debito tecnico milestone 3B"
```

---

## Definizione di "fatto" (3B)
- Il connettore `get_dns_events` normalizza le query DNS (respx).
- La source `"dns"` è attiva nell'ingest: gli eventi DNS finiscono in `events` (`source='dns'`), con la stessa idempotenza/dedup degli IDS.
- IDS e DNS coesistono in un singolo run; l'errore di una sorgente non blocca l'altra (test).
- Suite verde (nessun test 3A rotto dal `FakeClient` aggiornato).

---

## Debito tecnico (3B) — consolidato dalle review

- **Endpoint DNS DA VERIFICARE (sorgente più incerta)**: `unbound/diagnostics/queries` e il payload
  sono plausibili ma non confermati. Se OPNsense non espone i log DNS via API in modo usabile, valutare
  una sorgente alternativa (Zenarmor, export periodico) o syslog push per il DNS.
- **`since` non onorato anche per DNS** (come IDS): filtro client-side + dedup; rifinire col device reale.
- **Niente `dst_ip`/resolver per DNS** (`dst_ip=""`): se servisse il resolver upstream nei report,
  mapparlo dagli `attributes`.
- **Collasso di query DNS identiche ravvicinate** senza id sorgente (stesso ts+client+dominio+action →
  stesso hash → dedup le fonde): per i conteggi "hits per sito" potrebbe sottostimare. Valutare un
  contatore o l'id sorgente quando disponibile.
- **Cursore DNS non riverificato nei nuovi test** (review Task 2): l'avanzamento cursore per `source='dns'`
  è coperto solo indirettamente (la logica cursore è generica e già provata in 3A). Aggiungere
  un'asserzione esplicita se si vuole alzare la copertura.

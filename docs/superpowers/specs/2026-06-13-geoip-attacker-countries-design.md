# GeoIP attacker countries — design

**Date:** 2026-06-13 · **Status:** Approved direction (brainstorm Q&A: DB-IP Lite bundled + per-tenant Overview widget + **localized country names in v1**)

## Goal

Show **which countries the attacking IPs come from**, in two places:
- **Dashboard** — a "Top attacker countries" widget on the per-tenant **Overview** page.
- **Report** — a list of attacker countries with **counts and percentages** (a new toggleable report section).

Attacker IPs are already ingested: IDS events live in `events` (`source='ids'`) with a `src_ip` column; the
report's attacks block already ranks `src_ip`. This milestone maps `src_ip → country` and aggregates.

## GeoIP data source (offline — no runtime outbound to third parties)

**DB-IP Lite Country** (`.mmdb`, **CC-BY 4.0**, freely redistributable, no account/EULA). Read offline with
the `maxminddb` Python reader. The project's outbound-safety invariant forbids per-request third-party calls,
so resolution is a **local mmdb lookup** — never an online API.

**Distribution = pipeline + fetch (mirrors the catalog system the user already runs).** A scheduled GitHub
Action **`publish-geoip.yml`** (monthly + manual dispatch) downloads the latest DB-IP Lite Country mmdb,
records its SHA-256, and uploads it as an asset on a rolling **`geoip`** release. At runtime the app fetches
the asset on demand, **caches** the bytes + sha + version in a **global non-RLS `geoip_cache`** table (exactly
like `catalog_cache`; migration adds it), **verifies the SHA-256**, and loads an in-memory `maxminddb` reader
from the cached bytes — so subsequent lookups are fully offline. A periodic worker job (or first-use) refreshes
the cache when the release publishes a newer mmdb. Settings: `geoip_auto_fetch` (bool, default on) +
`geoip_release_base_url`. The single outbound fetch is to a trusted GitHub release URL, SHA-256-verified —
the same accepted pattern as catalogs (no per-request third-party calls). **Attribution:** DB-IP CC-BY credit
in the report + README + the Overview widget (a small "GeoIP: DB-IP" note). For dev/tests a tiny **fixture
mmdb** is vendored under `backend/tests/` so the suite never needs the live asset or network.

**Graceful degradation:** if the mmdb is unavailable, resolution returns `None` → IPs roll up as "Unknown",
the widget shows an empty/"no data" state, and the report section renders "No data" (never an error).

## Resolution semantics

- `app/services/geoip.py`: `GeoIp.country(ip) -> str | None` returning the **ISO 3166-1 alpha-2 code** (e.g.
  `"RU"`). Aggregation keys by **code**; the human name is resolved at render time per the viewer's locale
  (see "Localized country names"), so we never store/translate names ourselves.
  - Private/reserved/loopback/link-local IPs (RFC1918, CGNAT, etc.) → the sentinel code **`"PRIVATE"`**
    (not a country), so internal scanners don't masquerade as a geography.
  - Unparseable / not-found → `None` → the sentinel code **`"UNKNOWN"`**.
  - Reader is process-cached; cache invalidated when the `geoip_cache` version changes.

## Localized country names (v1)

We resolve `code → localized name` at render time from CLDR data — no hand-translated country tables:
- **Backend (report PDF, server-side):** **Babel** — `babel.Locale.parse(locale).territories.get(code)`.
  Babel ships CLDR territory names for every shipped locale (map the app's `zh`→`zh_Hans`, `zh-TW`→`zh_Hant`;
  the other 10 map directly). Add `babel` to backend deps. Fallback to the code if a name is missing.
- **Frontend (Overview widget, client-side):** the native **`Intl.DisplayNames([locale], {type: 'region'})
  .of(code)`** (no payload; `zh`/`zh-TW` resolve to Simplified/Traditional natively). Fallback to the code.
- The two **sentinels** (`PRIVATE`, `UNKNOWN`) are NOT ISO codes, so they get real i18n keys
  (`country_private`, `country_unknown`) in both the 12 report locales and the 12 UI locales.

## Backend

- **`aggregation.py`** — `attacker_countries(frm, to, device_id=None, limit=None) -> list[CountryCount]` where
  `CountryCount = (code, count, pct)` (name resolved at render):
  1. `SELECT src_ip, count(*) FROM events WHERE source='ids' AND src_ip<>'' AND time range [+ device]` GROUP BY src_ip.
  2. Resolve each distinct `src_ip → code` via `GeoIp` (in Python; the GROUP BY already collapses volume).
  3. Sum counts by code (+ `PRIVATE`/`UNKNOWN` sentinels), compute `pct = count/total*100`, sort desc,
     optional top-N (`limit`).
- **API** — `GET /api/tenants/{tid}/attacker-countries?frm&to[&device_id]` (`DEVICE_VIEW`) → `CountryCount[]`
  (codes + counts + pct; the frontend localizes the names).
- **Report** — new section key **`attacker_countries`** (default **ON**, client-facing). `build_context`
  builds an `AttackerCountriesBlock` whose rows carry the **localized** `name` (Babel, per the report locale)
  + `count` + `pct`; template renders a ranked table "Country | Attempts | %". Add to
  `SECTION_KEYS`/`BUILTIN_DEFAULTS` + the settings/schedule toggle UI (the per-section switch list already
  exists from the enrichment milestone).
- **`i18n.py`** — new report strings (`attacker_countries_title/explain`, `col_country`, `col_share`,
  `country_private`, `country_unknown`) across **all 12 locales** (parity guard). Country names themselves
  are NOT in i18n — Babel resolves them from the ISO code per locale.

## Frontend

- **Overview widget** — a "Top attacker countries" card (per-tenant) listing top N countries (name via
  `Intl.DisplayNames` from the code) with a count + a percentage bar. New hook over the new endpoint; gated by
  the existing tenant role (DEVICE_VIEW). New `overview.attackerCountries.*` i18n keys across 12 locales (the
  widget title/labels + the `country_private`/`country_unknown` sentinel names — NOT the country names).
- **Report section toggle** — the new `attacker_countries` switch appears automatically in the existing
  "Report sections" group (Report Settings) + per-device override (Report Schedule); add its label to the
  frontend `reports.sections` i18n namespace (12 locales).

## Testing

- `geoip` resolver: known public IP → expected country; RFC1918 → Private; garbage → None. (Use a tiny
  fixture mmdb or a vendored sample, not the full DB, so tests don't need the live asset.)
- `attacker_countries` aggregation: seeded IDS events across a few src_ips → correct per-country counts +
  percentages summing to ~100; device filter; empty range → [].
- API: endpoint returns the list, RBAC (read_only may view), tenant-scoped.
- Report: `attacker_countries` ON renders the country table; OFF hides it; i18n parity for the new keys.
- Frontend: the Overview widget renders the list; the new report-section switch toggles.

## Testing (localization additions)
- Babel resolves a sample code per locale (e.g. `RU` → "Russia"/"Russie"/"Russland"/"روسيا"/"ロシア"/"俄罗斯"),
  and `zh`→`zh_Hans` / `zh-TW`→`zh_Hant` mapping is correct; missing/garbage code falls back to the code.
- Sentinel labels (`country_private`, `country_unknown`) translate across all 12 report + UI locales (parity).

## Out of scope (v1)
- City/ASN granularity. A cross-tenant MSP geo view (per-tenant Overview only for v1). Map/choropleth
  visualization (a ranked list + bars, not a world map).
- GeoIP on non-IDS sources (DNS/dst_ip). Historical backfill of a per-event country column (resolve at query
  time, not at ingest — keeps ingest unchanged and lets the mmdb improve retroactively).

# GeoIP attacker countries — design

**Date:** 2026-06-13 · **Status:** Approved direction (brainstorm Q&A: DB-IP Lite bundled + per-tenant Overview widget)

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

**Distribution (mirrors the catalog pattern the user already runs):** a scheduled GitHub Action
`publish-geoip.yml` (monthly + manual dispatch) downloads the latest DB-IP Lite Country mmdb, records its
SHA-256, and uploads it as an asset on a rolling **`geoip`** release. The app fetches it on demand, caches the
bytes + sha + version in a **global non-RLS `geoip_cache`** table (exactly like `catalog_cache`), verifies the
SHA-256, and loads an in-memory `maxminddb` reader from the cached bytes. Settings: `geoip_auto_fetch` (bool)
+ `geoip_release_base_url`. **Attribution:** DB-IP CC-BY credit in the report footer area + README + the
Overview widget (a small "GeoIP: DB-IP" note).

**Graceful degradation:** if the mmdb is unavailable, resolution returns `None` → IPs roll up as "Unknown",
the widget shows an empty/"no data" state, and the report section renders "No data" (never an error).

## Resolution semantics

- `app/services/geoip.py`: `GeoIp.country(ip) -> CountryHit | None` where `CountryHit = (code, name)`.
  - Private/reserved/loopback/link-local IPs (RFC1918, CGNAT, etc.) → a sentinel **"Private/Internal"** bucket
    (not a country), so internal scanners don't masquerade as a geography.
  - Unparseable / not-found → `None` → **"Unknown"** bucket.
  - Reader is process-cached; cache invalidated when the `geoip_cache` version changes.

## Backend

- **`aggregation.py`** — `attacker_countries(frm, to, device_id=None, limit=None) -> list[CountryCount]` where
  `CountryCount = (code, name, count, pct)`:
  1. `SELECT src_ip, count(*) FROM events WHERE source='ids' AND src_ip<>'' AND time range [+ device]` GROUP BY src_ip.
  2. Resolve each distinct `src_ip → country` via `GeoIp` (in Python; the GROUP BY already collapses volume).
  3. Sum counts by country (+ "Private/Internal" + "Unknown" buckets), compute `pct = count/total*100`,
     sort desc, optional top-N (`limit`).
- **API** — `GET /api/tenants/{tid}/attacker-countries?frm&to[&device_id]` (`DEVICE_VIEW`) → `CountryCount[]`.
- **Report** — new section key **`attacker_countries`** (default **ON**, client-facing). `build_context`
  builds an `AttackerCountriesBlock` (rows: flag-less `name`, `count`, `pct`) when enabled; template renders a
  ranked table "Country | Attempts | %". Add to `SECTION_KEYS`/`BUILTIN_DEFAULTS` + the settings/schedule
  toggle UI (the per-section switch list already exists from the enrichment milestone).
- **`i18n.py`** — new report strings (`attacker_countries_title/explain`, `col_country`, `col_share`,
  `country_private`, `country_unknown`) across **all 12 locales** (parity guard). Country **names** come from
  the mmdb (English names); localizing country names themselves is out of scope (v1).

## Frontend

- **Overview widget** — a "Top attacker countries" card (per-tenant) listing top N countries with a count +
  a percentage bar. New hook over the new endpoint; gated by the existing tenant role (DEVICE_VIEW). New
  `overview.attackerCountries.*` i18n keys across 12 locales.
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

## Out of scope (v1)
- Localizing country names (mmdb English names shown). City/ASN granularity. A cross-tenant MSP geo view
  (per-tenant Overview only for v1). Map/choropleth visualization (a ranked list + bars, not a world map).
- GeoIP on non-IDS sources (DNS/dst_ip). Historical backfill of a per-event country column (resolve at query
  time, not at ingest — keeps ingest unchanged and lets the mmdb improve retroactively).

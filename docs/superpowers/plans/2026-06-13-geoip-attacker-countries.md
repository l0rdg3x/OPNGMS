# GeoIP attacker countries — implementation plan

> Spec: `docs/superpowers/specs/2026-06-13-geoip-attacker-countries-design.md`. Branch: `feat/geoip-attacker-countries`.
> Run backend tests after each backend task. Mirror the catalog fetch/cache pattern
> (`app/services/catalog_provider.py`, `app/models/catalog_cache.py`, `catalog_*` settings in `core/config.py`).

**Goal:** Resolve attacking IPs (IDS `events.src_ip`) → ISO country code (offline, DB-IP Lite mmdb fetched+cached
like catalogs), aggregate per country with counts + percentages, and surface it on the per-tenant Overview
dashboard + as a new toggleable report section. Country names localized at render (Babel server / Intl.DisplayNames client).

**New deps (backend `pyproject.toml`):** `maxminddb>=2.6` (mmdb reader), `babel>=2.14` (CLDR country names).

---

## Task 1 — GeoIP cache model + migration + settings

- `app/models/geoip_cache.py` — `GeoipCache(UUIDPKMixin)`: `source: str` (e.g. "dbip-country"), `sha256: str`,
  `mmdb: bytes` (LargeBinary), `version: str`, `fetched_at`. UNIQUE(source). Org-level (NOT in TENANT_TABLES).
  Register in `models/__init__.py`.
- Migration `0032_geoip_cache.py` (`down_revision="0031"`): create the table; reapply `grant_app_role_statements()`
  (mirror `0028_catalog_cache.py`).
- `core/config.py`: add `geoip_auto_fetch: bool = True` and `geoip_release_base_url: str = "<rolling geoip release assets URL>"`.

## Task 2 — GeoIP provider/service

- `app/services/geoip_provider.py` — fetch + cache, mirroring `catalog_provider.py`:
  `async def get_reader(session) -> maxminddb.Reader | None` — return a process-cached reader; on cache-miss
  (and `geoip_auto_fetch`), fetch the mmdb asset (httpx via the SSRF-aware client used for catalogs if applicable,
  else a plain GET to the trusted release URL), verify SHA-256 against the release manifest, store in `geoip_cache`,
  build the reader from bytes. Returns None (degrade) if unavailable. Invalidate the in-process reader when the
  cached `version` changes.
- `app/services/geoip.py` — `class GeoIp` wrapping a reader: `country(ip: str) -> str | None`:
  parse with `ipaddress`; private/loopback/link-local/reserved → `"PRIVATE"`; lookup → alpha-2 code; miss → None.
  A module helper `localized_country_name(code, locale) -> str` using Babel (`Locale.parse(_babel_locale(locale))
  .territories.get(code, code)`; map `zh`→`zh_Hans`, `zh-TW`→`zh_Hant`). `PRIVATE`/`UNKNOWN`/unknown code → caller
  substitutes the i18n sentinel / the code.

## Task 3 — Aggregation + API

- `app/services/reporting/aggregation.py` — `attacker_countries(*, frm, to, device_id=None, limit=None,
  geoip) -> list[CountryCount]` where `CountryCount=(code, count, pct)`: GROUP BY src_ip over `events`
  (`source='ids'`, `src_ip<>''`, time range, optional device); resolve each src_ip→code via `geoip.country`;
  sum by code (+ PRIVATE/UNKNOWN), pct=count/total*100, sort desc, optional top-N. (geoip injected so it's testable.)
- `app/api/overview.py` (or the existing overview/monitoring router) — `GET /api/tenants/{tid}/attacker-countries
  ?frm&to[&device_id]` (`require_tenant(DEVICE_VIEW)`) → `list[{code, count, pct}]`. Resolve the reader via the
  provider; degrade to `[]` if no mmdb. Add a Pydantic `CountryCountOut`.

## Task 4 — Report section (`attacker_countries`)

- `sections.py` — add `"attacker_countries"` to `SECTION_KEYS` + `BUILTIN_DEFAULTS` (True).
- `context.py` — `AttackerCountriesBlock(rows: list[CountryRow], )` where `CountryRow=(name, count, pct)` (name
  already localized via Babel for the report locale); `build_context` builds it when `enabled["attacker_countries"]`
  (needs a GeoIp reader — obtain via the provider; degrade to an empty/None block if no mmdb). Place after the
  attacks block.
- `templates/report.html.j2` + `report.css` — ranked table "Country | Attempts | %" + the `attacker_countries_*`
  title/explain. DB-IP attribution line in the footer/section.
- `i18n.py` — add `attacker_countries_title`, `attacker_countries_explain`, `col_country`, `col_share`,
  `country_private`, `country_unknown`, `geoip_attribution` across **all 12 report locales** (parity guard).

## Task 5 — Frontend: Overview widget + report-section toggle label

- `gen:api` after Task 3. New hook `src/overview/attackerCountriesHooks.ts` over the endpoint.
- Overview page: a "Top attacker countries" card — country name via `new Intl.DisplayNames([locale],
  {type:'region'}).of(code)` (fallback to code; PRIVATE/UNKNOWN → i18n sentinels), count + a % bar.
- i18n: `overview.attackerCountries.*` (title, empty, attribution) + `country_private`/`country_unknown` +
  the new `reports.sections.attacker_countries` label across **all 12 UI locales** (parity, compiler-enforced).

## Task 6 — Publish Action + docs

- `.github/workflows/publish-geoip.yml` — monthly + manual: download DB-IP Lite Country mmdb, compute SHA-256,
  upload to the rolling `geoip` release with a manifest. (Mirror `publish-catalogs.yml`.)
- README/wiki: DB-IP CC-BY attribution + a note on the new section/widget.

## Task 7 — Tests
- `geoip`: fixture mmdb (vendored, tiny) → public IP→expected code, RFC1918→PRIVATE, garbage→None; Babel
  localization (RU→Russia/Russie/Russland/روسيا/ロシア/俄罗斯; zh/zh-TW mapping); sentinel i18n parity.
- `attacker_countries` aggregation (inject a fake geoip): counts + pct sum ~100, device filter, empty→[].
- API: list + RBAC (read_only views) + tenant scope + degrade-to-[] when no mmdb.
- Report: section ON renders the table, OFF hides it; report i18n parity for the new keys.
- Frontend: widget renders + localizes; the new report-section switch appears.

## Task 8 — security-review (outbound fetch + SHA-256 verify + no per-request third-party calls) → PR.

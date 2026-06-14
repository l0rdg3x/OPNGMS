# Changelog

All notable changes to OPNGMS are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every tagged release also appears on the
[GitHub Releases page](https://github.com/l0rdg3x/OPNGMS/releases) — published automatically from the
annotated tag when a version is cut.

> **Pre-1.0:** OPNGMS is complete and deployable, but the API is not yet frozen.

## [Unreleased]

## [0.9.0] - 2026-06-14
### Added
- **Security / Perimeter.** Two new perimeter threat signals, surfaced per tenant: **failed logins** to
  the box (attacker IP + attempted username, parsed from the OPNsense audit log) and **firewall blocks**
  (attacker IP + targeted port, from the structured firewall log). Both resolve the attacker IP to a
  country via the existing GeoIP layer. They appear as summary cards on the Overview and on a dedicated
  **Perimeter** page (ranked per-IP, with a 24h/7d/30d window), and as two PDF report sections that are
  **toggled per device** (on the device detail page). A bounded `perimeter_attacker` rollup (per
  device/kind/source-IP) keeps storage small regardless of traffic volume, fed by a per-device ingest
  that reuses the existing connector + cursor machinery; a daily retention sweep prunes stale rows.
  Tenant-isolated via RLS; the ingest + the new endpoint were security-reviewed. (#145, #146, #147, #148)

## [0.8.0] - 2026-06-14
### Added
- **Configurable deployment tunables.** Operational knobs are now configurable without forking. Four
  boot-time settings come from `.env` — the ARQ worker concurrency (`WORKER_MAX_JOBS`), the SQLAlchemy
  pool (`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`), and the OPNsense connector timeout (`OPNSENSE_HTTP_TIMEOUT`)
  — alongside a comprehensive `.env.example`. Ten more are editable **live** from the superadmin
  **System → Runtime settings** page (the env/code value is the default; a DB override wins): the
  firmware poll budget, catalog / GeoIP auto-fetch, the silent-tenant detector switch + threshold, the
  login brute-force limits, and the session TTL + idle timeout. A small generic registry over the
  existing `app_setting` store backs `GET`/`PUT /api/admin/settings` (superadmin, CSRF, audited).
  Defaults preserve previous behavior. (#139, #140, #141, #142)
### Changed
- **The whole UI is now translated.** The System page and the Log fleet page — previously hardcoded
  English — are fully translated across all 12 locales (the live-push toggle, the new Runtime settings
  form, and the entire Log fleet dashboard). (#142, #143)
### Fixed
- Release the per-call ARQ pool with the redis `aclose()` API instead of the deprecated `close()`,
  clearing the last deprecated redis lifecycle call. (#138)

## [0.7.0] - 2026-06-14
### Added
- **Community plugin coverage & lifecycle.** The version-aware catalog now covers `opnsense/plugins` (a
  separate per-version asset, versioned 1:1 with core, published alongside the core catalogs). A per-device
  **Plugins** page lists the plugins the box reports — badged installed / available / locked + version —
  with search and **Install / Remove** (through the existing gated firmware-action pipeline), plus a
  **Configure** drawer to edit an installed plugin's configuration via the existing catalog apply pipeline.
  (#132, #134, #135, #136)
- **Changelog + automatic GitHub Releases.** `CHANGELOG.md` (Keep a Changelog) and a workflow that
  publishes a GitHub Release from the annotated tag on every `vX.Y.Z` push. (#131)
### Docs
- A **Scope & limitations** section (README + Wiki): OPNGMS manages firewalls through the OPNsense API and
  builds its catalog from public `opnsense/core` + `opnsense/plugins`, so it's bounded by that API
  (no firmware rollback / config restore; non-MVC settings are read-only) and proprietary / Business-only
  plugins are not covered. (#133)

## [0.6.0] - 2026-06-13
### Added
- World choropleth **map** of attacker countries, shaded by attack share (%), in both the per-tenant
  Overview (interactive — hover, pan, zoom) and the PDF report (static SVG). Complements the ranked
  list; `PRIVATE`/`UNKNOWN` origins are excluded from the map. (#130)
- Assigned interface / gateway / VPN **names** instead of raw OPNsense identifiers (with fallback to the
  id), reflected in the Health tab and in reports. (#127)
### Fixed
- `rekey_secrets` now re-encrypts **all** encrypted columns (MFA TOTP secret, SMTP password, syslog CA
  key), so rotating `MASTER_KEY` no longer corrupts them; added a metadata guard test. (#128)
### Security
- Pinned `d3-color` to 3.1.0 to clear the react-simple-maps ReDoS advisory (GHSA-36jr-mh4h-2g58). (#130)
### Docs
- README screenshot gallery moved to the Wiki + a clearer `MASTER_KEY` rotation walkthrough; demo reports
  regenerated to reflect only the enabled report sections. (#129)

## [0.5.0] - 2026-06-13
### Added
- Per-tenant "Top attacker countries" Overview widget + a report `attacker_countries` section, using an
  offline DB-IP Lite database (monthly auto-published) with localized country names. (#123)
### Fixed
- Dynamic byte units (GB) on the device-health traffic charts. (#124)
- Legend on multi-line device-health charts. (#126)
- `publish-geoip` workflow now works without a repository checkout. (#125)

## [0.4.0] - 2026-06-13
### Added
- **Group-based RBAC** layered over membership: a group grants a tenant role
  (tenant_admin / operator / read_only) over a wildcard (all tenants) or a specific tenant; effective
  access is the highest of direct membership + group grants. Org/critical actions stay superadmin-only,
  with a superadmin-only Groups admin page. Security-reviewed. (#121)

## [0.3.0] - 2026-06-13
### Added
- Four toggleable enriched report sections (executive summary, device health, alerts & connectivity,
  firmware & configuration) with tenant defaults + per-device overrides, across all 12 locales. (#120)
- Immediate first poll + config backup when a reachable device is onboarded. (#118)
### Fixed
- Superadmins now see the tenant-admin / operator UI surface (Reports, Logs, etc.). (#119)

## [0.2.0] - 2026-06-13
### Added
- 12-language UI (including full RTL / Arabic) and 12-language PDF reports.
- Config editor completed (sub-project 3c): cross-version diff badges + the read-only live config.xml map.
### Changed
- Catalogs published from OPNsense 25.x onwards (previously 26.1+).
- Hypertable columns use `TEXT` instead of `VARCHAR`.
### Security
- CodeQL extended suite; third-party actions pinned to commit SHAs; partial-SSRF fix in the catalog fetch.
### Docs
- Comprehensive Wiki, slimmed README, `AGENTS.md` for LLM contributors, issue templates, refreshed
  screenshots, OPNsense trademark disclaimer.

## [0.1.0] - 2026-06-13
### Added
- First tagged release: a multi-tenant MSP console for OPNsense — auth / RBAC + MFA, device onboarding,
  monitoring + alerting, PDF reporting, the version-aware config catalog editor (generator + dynamic
  distribution + apply engine + editor UI), the syslog log lake, and the Docker deployment stack.

[Unreleased]: https://github.com/l0rdg3x/OPNGMS/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/l0rdg3x/OPNGMS/releases/tag/v0.1.0

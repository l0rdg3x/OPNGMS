# Changelog

All notable changes to OPNGMS are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every tagged release also appears on the
[GitHub Releases page](https://github.com/l0rdg3x/OPNGMS/releases) — published automatically from the
annotated tag when a version is cut.

> **Pre-1.0:** OPNGMS is complete and deployable, but the API is not yet frozen.

## [Unreleased]

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

[Unreleased]: https://github.com/l0rdg3x/OPNGMS/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/l0rdg3x/OPNGMS/releases/tag/v0.1.0

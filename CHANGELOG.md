# Changelog

All notable changes to OPNGMS are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every tagged release also appears on the
[GitHub Releases page](https://github.com/l0rdg3x/OPNGMS/releases) — published automatically from the
annotated tag when a version is cut.

> **Pre-1.0:** OPNGMS is complete and deployable, but the API is not yet frozen.

## [Unreleased]

## [0.19.0] - 2026-06-16
### Added
- **OAuth2 (XOAUTH2) authentication for the SMTP relay — Gmail + Microsoft 365.** Google Workspace and
  Microsoft 365 are disabling basic-auth SMTP, so the report-email relay can now authenticate with
  **OAuth2** instead of a password. In **System → SMTP**, pick *OAuth2*, choose the provider (Google /
  Microsoft 365), and provide the `client_id` / `client_secret` / `refresh_token` (plus the Azure `tenant`
  for Microsoft); OPNGMS exchanges the refresh token for a short-lived access token at send time and
  authenticates with **SASL XOAUTH2** (via aiosmtplib's native support). Password auth is unchanged. The
  OAuth `client_secret` + `refresh_token` are **Fernet-encrypted** (`MASTER_KEY`) like every other secret —
  never returned by the API (only `has_…` flags) and never logged; the access token lives only in memory
  for the send. The Send-test button exercises the OAuth path too, and the MASTER_KEY rekey tooling covers
  the new encrypted columns. This is the manual-entry core; an in-app **"Connect"** button (OAuth
  authorization-code callback) is a planned follow-up for deployments with a public callback URL. Hardened
  against the CodeQL extended suite (the Microsoft tenant is sink-guarded against partial-SSRF). (#205)

## [0.18.1] - 2026-06-16
### Fixed
- **Business→Community map dropped Business hotfix releases.** v0.18.0's `business-base.json` generator
  only matched a *"based on the OPNsense X.Y.Z **community** version"* header, but a Business **hotfix**
  (e.g. `25.4.3`, `24.4.3`) is instead *"based on the OPNsense X.Y.Z **business** version"* — it chains onto
  the prior Business release. Those entries silently fell out of the map. The generator now captures the
  `community`/`business` kind and **follows a business→business chain transitively** to the underlying
  Community base (`25.4.3 → 25.4.2 → 25.1.12`), skipping only genuinely unresolvable chains or cycles. It
  also no longer floors the map at major ≥ 25 (the Community-catalog floor is enforced downstream by the
  resolver), so the published map is **complete** — every documented Business release (38 entries from the
  current `opnsense/changelog`) resolves to its Community base instead of just the 8 direct-Community ones.

## [0.18.0] - 2026-06-16
### Changed
- **Business→Community catalog mapping now per-sub-version, from `opnsense/changelog`.** A Business box is
  served the Community catalog of the OPNsense version its Business release is based on (Business = a
  stabilized snapshot of a specific Community version; the core API and config models are shared). That
  base-version map (`business-base.json`) was previously scraped from `docs.opnsense.org`, which only has a
  page **per Business major** — so a `26.4.1` device (truly based on `26.1.9`) floor-resolved to the `26.4`
  entry (`26.1.6`) and got a slightly less-accurate catalog. The map is now generated from the
  [`opnsense/changelog`](https://github.com/opnsense/changelog) repo's `business/` tree — **one entry per
  Business sub-version**, parsed from each file's *"based on the OPNsense X.Y.Z community version"* header —
  so each Business device resolves to the **most accurate** Community base catalog (e.g. `26.4.1 → 26.1.9`,
  `25.10.1 → 25.7.8`). The source is version-controlled plaintext rather than scraped HTML. This is the last
  piece of the version-aware config editor program (sub-project 4); editable schemas for Business
  **proprietary** plugins remain out of scope (their MVC models are not published publicly). The resolver
  was unchanged — it already floor-resolves the denser map. Behavior-preserving for Community devices.

## [0.17.0] - 2026-06-16
### Added
- **Management-IP attribution for config-change audit.** OPNGMS now **auto-learns the source IP it
  manages each box from** and uses it to disambiguate the previously-opaque `api` channel: it correlates
  the box's API config-changes with OPNGMS's own apply ledger (an applied change within a few minutes of
  the logged API change) and, when the correlated changes agree on a single IP, learns that IP as the
  device's management IP. An `api` change is then split into **`opngms`** (OPNGMS's own change, from the
  learned IP — expected/benign) versus **`api_external`** (an API change from any other IP — a change made
  outside OPNGMS, so **drift**, raised at higher severity and alerted alongside WebGUI/console edits). The
  learning is conservative (a single unambiguous IP) and **self-correcting** (a clean OPNGMS apply re-learns
  the right IP), and the pipeline is a no-op until the IP is learned. The new channels are labelled across
  the device **Config changes** tab, the Overview direct-changes card, and the per-client PDF report
  (`OPNGMS` / `External API`) in all 12 UI + 12 report languages, and `api_external` counts as a direct/drift
  channel everywhere `gui`/`system` do. Resolves the "management-IP attribution is a documented follow-up"
  note from the v0.16.0 config-change-audit milestone. Live-verified end-to-end against the test box
  (auto-learned `192.168.6.100`; OPNGMS-applied changes reclassified to `opngms`). (#199, #200)

## [0.16.0] - 2026-06-16
### Added
- **Config-change audit ("who changed the box, and how").** OPNGMS now ingests the OPNsense **audit log**
  into a new `config_audit` events source (reusing the per-source cursor, dedup, and retention), surfacing
  **who/what/when changed a device's config** with a **best-effort drift-cause attribution**: each change
  is classified by **channel** from the request path — `api` (programmatic: OPNGMS or another API client),
  `gui` (a human in the WebGUI), or `system` (console / script). A **direct on-box** change (gui/system —
  OPNGMS only ever writes via the API) is the **drift** signal: it is recorded at higher severity and
  raises a **deduped alert**. Each device gains a **Config changes** tab (time · area · actor · IP ·
  channel · change, with a *Direct* badge on drift rows), the Overview gains a fleet **direct-changes
  (24h)** card, and the per-client PDF gains a **Config changes** section (by-channel breakdown + recent
  changes, default on, standard toggle model). Second of the two diagnostic-log milestones. (#184, #185,
  #186)

  > Best-effort by design: the `api` channel can't separate OPNGMS from a human using a modern WebGUI MVC
  > page (both hit `/api/…`) — the strong drift signals are the gui/console channel and the actor IP.
  > Management-IP attribution is a documented follow-up. Distinct from the superadmin **Audit viewer**
  > (v0.10.0), which is OPNGMS's *own* write-ledger; this is the *box's* config log.

## [0.15.0] - 2026-06-15
### Added
- **Service / reliability events.** OPNGMS now surfaces a device's **reliability** from the OPNsense
  system log: it classifies **reboots, service crashes/restarts, and disk/FS warnings** (a curated,
  fail-safe rule set — only recognized events are stored, not the whole log) into the events store as a new
  `service` source, reusing the existing per-source cursor, dedup, and retention. Each device gains a
  **Reliability** tab with a paginated timeline (time · category · name · severity · process/message), the
  Overview gains a fleet **service-events (24h)** card, the per-client PDF gains a **Reliability** section
  (default on, standard toggle model), and a new high-severity event (a reboot / crash / disk-full) raises
  a **deduped alert**. First of the two diagnostic-log milestones. (#178, #179, #180)

  > The classifier's reboot/crash/disk line patterns are a curated starter set (an idle box shows none of
  > these), tuned against real events on the box as a runtime-verified follow-up.

## [0.14.0] - 2026-06-15
### Added
- **Operator Revert now covers every live-applied config kind.** The targeted-inverse **Revert** (undo a
  config push by generating its inverse through the normal apply pipeline) previously worked only for
  firewall aliases and generic settings. It now also reverts **firewall rules**, **Monit tests**, **IDS
  policies**, and the version-aware editor's **catalog settings** — so the Revert button is enabled across
  the board. Each inverse is reconstructed purely from the encrypted pre-apply snapshot: an *update* is
  restored to its prior record, a *creation* is deleted, and a catalog change's scalar + grid edits are
  individually inverted (a deleted grid row re-added, a modified row restored, an added row deleted by its
  box-assigned id). Reverting an unrecoverable snapshot fails closed with a clear reason rather than a
  partial undo. Behind the `LIVE_PUSH_ENABLED` master switch like every push. `ids_rulesets` stays out of
  scope (its apply is additive). (#174, #175, #176)
### Changed
- The connector gained `delete` operations for firewall rules and Monit tests (used by the revert path);
  box-sourced UUIDs are now charset-validated before being embedded in any request path. (#174)

## [0.13.0] - 2026-06-15
### Added
- **IDS policy templates.** A new curated MSP template kind — `ids_policy` — lets you define a Suricata/IDS
  **policy** (rule-action tuning) once in the template library and apply it across the fleet, alongside the
  existing alias / setting / ruleset / firewall-rule / Monit-test kinds. A policy matches rules by
  {ruleset, current action, rule-metadata filters} and sets them to **alert / drop / disable** with a
  priority — the standard way to turn a noisy ruleset into a useful one (e.g. "drop the ET-malware
  category, alert-only on info severity"). Authored from a new template-library form (rulesets picked from
  a reference device, plus an advanced metadata-filter editor); the connector upserts the policy by
  description and resolves ruleset filenames to the device's **enabled** rulesets at apply time (a
  disabled/absent ruleset is refused, never partially applied). Add/set/**delete** are all supported, so a
  policy revert is a clean follow-up. Behind the `LIVE_PUSH_ENABLED` master switch like every push; UI
  translated across all 12 locales. (#171, #172)

## [0.12.0] - 2026-06-15
### Added
- **Log-forwarding hard revocation (CRL).** Revoking a device's log forwarding is now **enforced at the
  syslog-ng receiver**, not just on the box: the worker rebuilds a CA-signed **CRL** from the
  `revoked_syslog_certs` ledger (across all tenants) and writes it onto the shared cert volume; the
  syslog-ng container enforces it via `crl-dir()` with a small reload-watcher (it caches the CRL at
  startup, so the watcher runs `syslog-ng-ctl reload` when the CRL changes). A revoked device cert is then
  rejected at the TLS handshake — so a **stolen device key can no longer ship forged logs** by connecting
  directly to the receiver after revocation. A daily cron keeps the CRL fresh; revoke enqueues an
  immediate refresh. Defense-in-depth on top of short certs + auto-renew. Verified end-to-end against
  syslog-ng 4.5.0. (#169)
### Changed
- **Least-privilege syslog CA key.** The internal CA's encrypted **private key** moved out of `syslog_ca`
  into an owner-only `syslog_ca_key` table that the non-superuser API role (`opngms_app`) **cannot read**;
  the cert-signing path reaches it only through a single `SECURITY DEFINER` accessor function. This removes
  the key from the blanket `SELECT` grant so a read primitive can't exfiltrate it. CA **creation** is now
  owner-only (bootstrap/worker); the API returns HTTP 503 if log forwarding is enabled before the CA is
  bootstrapped. Security-reviewed. (#168)
- Verified at a staging bring-up (not CI): the syslog-ng → OpenSearch **document field shape** (per-tenant
  index naming from the cert `O=`/`CN=`, and the field types the Logs search relies on) and **multi-node
  HA** (one node lost → the index stays available with no data loss, replicas re-allocate to the survivors).

## [0.11.0] - 2026-06-15
### Added
- **Configurable data retention — global default + per-tenant override.** How long the data behind the
  dashboards and reports is kept is now operator-configurable: a global default per data store (superadmin,
  **System → Runtime settings**) plus an optional **per-tenant override** (from each tenant's settings page,
  so every MSP client controls its own retention). Covers all four stores — the perimeter rollup (failed
  logins + firewall blocks), the IDS/DNS event history, device-health metrics, and the OpenSearch **log
  lake**. The three Postgres stores are swept by tenant-aware purge jobs that replace the previous fixed
  global TimescaleDB policies; the log lake now writes **per-tenant daily indices**
  (`opngms-logs-<tenant>-DATE`) pruned by a worker job that replaces the global ISM policy. Defaults preserve
  prior behavior (perimeter/metrics/log-lake 30 days, events 90). (#157, #158, #159, #163, #164)
- **Report ↔ retention consistency.** A report can no longer be configured to cover more days than the
  tenant's effective retention for the data its enabled sections use: on-demand and scheduled reports are
  **blocked** when over-long, and lowering retention surfaces a **warning** on the affected tenant's page —
  plus, for a global lowering, the **list of impacted tenants** to the superadmin. No silent clamping.
  (#160, #161, #162)

## [0.10.0] - 2026-06-14
### Added
- **Audit viewer (superadmin).** A new superadmin-only **Audit** page (`/admin/audit`) over the existing
  application audit ledger: every recorded action — actor (with email), tenant, action, target, IP and
  details — is browsable with filters (actor email, tenant, action, date range), offset pagination, and a
  CSV export. Backed by `GET /api/admin/audit` + `/export.csv` (superadmin-gated — the `AUDIT_VIEW`
  permission is now org-level), with actor→email / tenant→name enrichment and a supporting `(action, ts)`
  index. Security-reviewed. (#153, #154, #155)
### Changed
- **Complete, regression-proof audit coverage.** Mutating routes that previously recorded no audit entry now
  do — firmware actions (upgrade / reboot / plugin install-remove), first-superadmin setup, the immediate
  report send, and MFA enrollment start. A new CI guard test fails the build if any future mutating route
  ships without an audit record (or an explicit read-only exemption), so coverage stays complete. (#153)

## [0.9.1] - 2026-06-14
### Changed
- The two **perimeter** report sections — "Failed logins" and "Firewall blocks" — are now toggled like
  every other report section (from **Report settings**, with a per-tenant default and an optional
  per-schedule override), instead of the per-device toggle that shipped in v0.9.0. The
  `devices.report_perimeter` column is dropped (migration 0036). (#150)

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

[Unreleased]: https://github.com/l0rdg3x/OPNGMS/compare/v0.11.0...HEAD
[0.11.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.9.1...v0.10.0
[0.9.1]: https://github.com/l0rdg3x/OPNGMS/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/l0rdg3x/OPNGMS/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/l0rdg3x/OPNGMS/releases/tag/v0.1.0

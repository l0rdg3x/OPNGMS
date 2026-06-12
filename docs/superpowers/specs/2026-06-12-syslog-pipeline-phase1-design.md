# Syslog → OpenSearch Log Pipeline — Phase 1 Design Spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 1 of 3** (foundation). Phases 2 (search UI) and 3 (lifecycle/scale) follow.

## Goal

Stand up a **push-based log pipeline** for forensic/incident analysis: OPNsense devices ship their raw
logs (firewall `filterlog` + Suricata EVE) over **mTLS syslog** to an OPNGMS-run **syslog-ng receiver**
that writes them into **OpenSearch**, each document cryptographically attributed to `{tenant_id,
device_id}` from the device's client certificate. This complements — does not replace — the existing
pull-based API event ingest (IDS/DNS into the TimescaleDB `events` hypertable, which stays for curated
events/metrics). **Phase 1 deliverable: logs flowing into OpenSearch, tenant-isolated, provisioned
per-device — NO search UI yet** (that is Phase 2).

## Locked decisions (from brainstorming)

- **Storage = OpenSearch** (Apache-2.0) for the raw log lake; TimescaleDB stays for events/metrics.
- **Auth = mTLS with per-device client certs** from an internal OPNGMS CA.
- **Analysis = backend-mediated, always tenant-scoped** (Phase 2; the browser never touches OpenSearch).
- **Phase 1 design choices (this spec):** client-cert subject `CN=<device_id>, O=<tenant_id>`;
  OpenSearch + syslog-ng ship as an **opt-in** `docker-compose.logs.yml` override (off by default);
  the receiver is **syslog-ng config-only** (native OpenSearch destination, no custom receiver code);
  the CA is **backend-held** with its private key encrypted at rest; a shared **time-based index**
  (`opngms-logs-*`) with `tenant_id`/`device_id` fields + **ISM** age-based retention; provisioning is
  a **backend API endpoint** (the "enable forwarding" button is Phase 3 UX).

## Feasibility (verified on the real box, read-only)

OPNsense 26.1.9 (`/api/syslog/settings/getDestination`) exposes a remote-syslog destination with
`transport ∈ {udp4,tcp4,udp6,tcp6,**tls4,tls6**}` and a **`certificate`** selector (the client cert the
box presents) — i.e. genuine **client-cert mTLS**. The `trust/ca` and `trust/cert` APIs (verified 200)
allow importing the CA + a per-device client cert into the box's store. So the full chain — issue cert →
import into box → configure a `tls4` destination referencing it → device ships logs presenting the
per-device cert — is supported via the existing connector boundary.

## Architecture

```
   ┌─────────────┐   provisioning (backend, via connector):                ┌──────────────────────┐
   │ OPNGMS CA   │   1. issue device cert (CN=device_id, O=tenant_id)       │  OPNsense device     │
   │ (backend,   │──▶2. import CA + client cert into box (trust/cert API) ─▶│  ships filterlog +   │
   │ key enc'd)  │   3. add syslog dest (tls4 + cert + receiver host:port)  │  Suricata EVE        │
   └─────────────┘   4. reconfigure + verify                                └──────────┬───────────┘
          │ issues receiver server cert                                                │ mTLS syslog (6514)
          ▼                                                                            ▼
   ┌──────────────────────────────────────────────────────────────────────────────────────────┐
   │ syslog-ng receiver (container, config-only)                                                │
   │  source: TLS, peer-verify(required-trusted) against the OPNGMS CA  → rejects non-CA certs  │
   │  extract device_id = ${.tls.x509_cn}, tenant_id = O from ${.tls.x509_subject}              │
   │  parse: filterlog (csv/pattern) + Suricata EVE (json)                                      │
   │  destination: opensearch()/http _bulk → index opngms-logs-YYYY.MM.DD, doc {tenant_id,      │
   │               device_id, time, host, program, parsed fields, raw message}                  │
   └──────────────────────────────────────────────────────────────────┬───────────────────────┘
                                                                        ▼
                       OpenSearch (single node, internal network only) + ISM age-based retention
```

## Components

### 1. OPNGMS internal CA — `app/services/syslog_ca.py`

A backend-held CA that signs (a) the syslog-ng receiver's **server** cert and (b) per-device **client**
certs. Built on the `cryptography` library (already a dependency).

- **Storage:** a singleton table `syslog_ca` — `cert_pem` (public), `key_enc` (CA private key, Fernet
  via `MASTER_KEY`), `created_at`. Created lazily on first provisioning (`ensure_ca()`).
- **API:**
  - `ensure_ca() -> SyslogCa` — generate the CA (self-signed root, `keyCertSign`+`cRLSign`, ~10y) if absent.
  - `issue_server_cert(*, hostname: str) -> (cert_pem, key_pem)` — CN/SAN = receiver host (so boxes
    validate the server). Server EKU.
  - `issue_device_cert(*, tenant_id, device_id) -> (cert_pem, key_pem)` — **subject `CN=<device_id>,
    O=<tenant_id>`**, client EKU, ~2y validity, a random serial recorded for later revocation.
- The CA **public** cert is what the receiver (to verify clients) and the boxes (to trust the server)
  receive; the CA **private** key never leaves the backend.

### 2. syslog-ng receiver — `deploy/syslog-ng/` (config-only container)

A `syslog-ng` container (image with an OpenSearch/HTTP destination) configured — no custom code — to:

- **source** `s_tls`: `network(transport(tls) port(6514) tls(ca-file(<CA.pem>) cert-file(<server.pem>)
  key-file(<server.key>) peer-verify(required-trusted)))` → only CA-signed client certs are accepted.
- **attribute:** set `tenant_id`/`device_id` from the *verified* peer cert: `device_id = ${.tls.x509_cn}`;
  `tenant_id` parsed from `${.tls.x509_subject}` (the `O=` RDN) via a `subst`/`csv-parser`. These come
  from the cert, **never** the log payload — so a device can only write as itself.
- **parse:** `filterlog` via a pattern/csv parser; Suricata **EVE** via `json-parser`. Unparsed lines
  still index with the raw `message`.
- **destination** `d_opensearch`: the native `opensearch()` (or `http()` to the `_bulk` API) writing to
  index `opngms-logs-${YEAR}.${MONTH}.${DAY}` with `{tenant_id, device_id, @timestamp, host, program,
  …parsed…, message}`.

The receiver mounts three files the backend produces: the CA public cert, and its own server
cert+key (issued by `issue_server_cert`). A small bootstrap (init step) writes them before syslog-ng
starts.

### 3. OpenSearch — single node (opt-in container)

`opensearchproject/opensearch` (Apache-2.0), single node, **not published** (internal compose network
only; the receiver and — in Phase 2 — the backend are the only clients). A bootstrap applies:
- an **index template** for `opngms-logs-*`: `tenant_id`/`device_id`/`host`/`program` as `keyword`,
  `@timestamp` as `date`, `message` as `text`, parsed fields typed sensibly.
- an **ISM policy** rolling/deleting `opngms-logs-*` by age (default retention configurable, e.g. 30d).

### 4. Per-device provisioning — `app/api/log_forwarding.py` + connector methods

`POST /api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/enable` (RBAC `CONFIG_PUSH`, CSRF,
audited). Flow:
1. `ensure_ca()`; `issue_device_cert(tenant_id, device_id)`.
2. Import the **CA** (so the box trusts the receiver's server cert) and the **client cert+key** into
   the box's store — new connector methods `import_ca(pem)` / `import_cert(cert_pem, key_pem)` over the
   `trust/ca` + `trust/cert` APIs (verified live, revertibly).
3. `add_syslog_destination(...)` — `syslog/settings/addDestination` with `transport=tls4`,
   `certificate=<imported uuid>`, `hostname=<RECEIVER_HOST>`, `port=<RECEIVER_PORT>`, `rfc5424=1`,
   then `syslog/service/reconfigure`.
4. Record `device_log_forwarding` (device_id, enabled, cert serial/fingerprint, the OPNsense cert +
   destination uuids, provisioned_at) for later verify/rotate/revoke (Phase 3). A `…/disable`
   counterpart removes the destination + cert (revertible cleanup).

Behind `LIVE_PUSH_ENABLED`-style safety is not required here (this is its own provisioning path), but
the connector mutations are revertible and live-verified on the box per the project's standard.

## Data model

- `syslog_ca` (singleton): `id` (=1 guard), `cert_pem` Text, `key_enc` LargeBinary, `created_at`.
- `device_log_forwarding`: `device_id` PK FK→devices, `tenant_id`, `enabled` bool, `cert_serial` str,
  `cert_fingerprint` str, `opnsense_ca_uuid` str|null, `opnsense_cert_uuid` str|null,
  `opnsense_dest_uuid` str|null, `provisioned_at`, `updated_at`. (Tenant-scoped → RLS, in `TENANT_TABLES`.)

Migration **0024**.

## Deployment (opt-in)

- `docker-compose.logs.yml` override (off by default): `opensearch` (internal-only) + `syslog-ng`
  (publishes the mTLS port `${SYSLOG_TLS_PORT:-6514}`) + a bootstrap. Brought up with
  `docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml up -d`.
- `.env.example` gains `SYSLOG_RECEIVER_HOST` (the public name/IP devices connect to), `SYSLOG_TLS_PORT`,
  `OPENSEARCH_*` creds, `LOG_RETENTION_DAYS`. README gets a "Log lake (optional)" section.
- The backend reads OpenSearch creds + the receiver host (for provisioning the box's destination) from
  env; in Phase 1 OpenSearch is written only by the receiver (the backend writes nothing to it yet).

## Security

- **Cryptographic tenant attribution:** the receiver derives `{tenant_id, device_id}` from the
  CA-verified client cert subject, never from log content. `peer-verify(required-trusted)` rejects any
  cert not signed by the OPNGMS CA → a device can only write as itself; no cross-tenant spoofing.
- **CA private key** is the crown jewel: stored Fernet-encrypted (`MASTER_KEY`), only the backend holds
  it; the receiver and boxes only ever get the CA **public** cert. Rotation/revocation = Phase 3.
- **The mTLS port (6514) is internet-facing** (devices dial in from anywhere) — mTLS is the only
  admission control (no valid client cert → TLS handshake fails). OpenSearch is **not** exposed.
- **OpenSearch** runs on the internal compose network with the security plugin + credentials; only the
  receiver writes; Phase 2's backend search client is the only reader, always tenant-scoped.
- Provisioning is `CONFIG_PUSH`-gated, CSRF-protected, audited; the per-device cert+key transit to the
  box over the existing SSRF-guarded, optionally-pinned connector TLS.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Box rejects cert/dest import | provisioning returns the connector error; nothing half-applied is recorded as enabled |
| Receiver gets a non-CA client cert | TLS handshake refused (`required-trusted`); nothing indexed |
| Unparseable log line | indexed with raw `message` + tags (no data loss) |
| OpenSearch down | syslog-ng disk-buffers (config) and retries; no device-side impact |
| `disable` after a partial enable | removes whatever uuids were recorded; idempotent |

## Testing

- **CA (unit):** `issue_device_cert` → subject `CN=device_id, O=tenant_id`, client EKU, chains to the
  CA; `issue_server_cert` → server EKU + SAN; `ensure_ca` idempotent; key round-trips through Fernet.
- **Provisioning (unit + live):** the connector `import_ca`/`import_cert`/`add_syslog_destination`
  against recorded OPNsense responses, plus a **live revertible** enable→verify→disable on the real box
  (cert appears in the trust store, a `tls4` destination is created referencing it, then cleaned up).
- **API:** enable/disable RBAC (CONFIG_PUSH), CSRF, tenant/device scoping, idempotency, `device_log_forwarding` state.
- **Pipeline (integration, scripted):** a compose-up of opensearch + syslog-ng; send a sample syslog
  line over mTLS with a test client cert (`CN=devX, O=tenantY`); assert a doc lands in
  `opngms-logs-*` with `tenant_id=tenantY, device_id=devX` and the parsed fields. (Heavier; runs as a
  dedicated integration script, not the unit suite.)
- **Migration 0024**, **RLS** on `device_log_forwarding`.

## Build phases (informs the plan; Phase 1 only)

- **A — CA + data model:** `syslog_ca`/`device_log_forwarding` models + migration 0024 + RLS; the CA
  service (issue server/device certs); unit tests.
- **B — Connector + provisioning API:** connector `import_ca`/`import_cert`/`add_syslog_destination`
  (+ remove counterparts); the enable/disable endpoints; live revertible box verify; tests.
- **C — Infra:** `docker-compose.logs.yml` (opensearch + syslog-ng + bootstrap), the syslog-ng config
  (mTLS source + cert-subject attribution + filterlog/EVE parse + OpenSearch destination), the
  OpenSearch index template + ISM policy; the scripted pipeline integration test; `.env.example` +
  README.

## Out of scope (Phase 2 / Phase 3)

- **Phase 2:** the tenant-scoped backend **search API** + in-app investigation UI (time range,
  full-text, field filters, table, raw doc).
- **Phase 3:** the "enable log forwarding" **UX button**, cert **rotation/revocation** (CRL/OCSP or
  re-issue), **multi-node** OpenSearch + MSP-admin dashboards.

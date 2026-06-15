# Syslog Phase 3 hardening — design

**Status:** approved (autonomous session, user-directed scope).
**Date:** 2026-06-15.
**Scope:** the four deferred items from the syslog Phase 3 backlog the user asked to build in autonomy:
**CRL hard-revoke**, **field-shape verification**, **HA multi-node verification**, **least-privilege
`syslog_ca`**. See the deferred-backlog memory `syslog-phase3-deferred-backlog`.

This is a *hardening* milestone on top of the shipped Phase 1–3 log pipeline (syslog-ng over mTLS →
OpenSearch, per-device client certs from an internal CA, soft-revoke ledger). It changes no user-facing
search/feature behavior; it closes security + reliability gaps.

---

## 0. Feasibility spike (already run, 2026-06-15) — drives the design

A throwaway docker bring-up (`balabit/syslog-ng:4.5.0`) **overturned the earlier assumption that
syslog-ng can't enforce a CRL.** Findings (these are design inputs, not aspirations):

- **`crl-dir()` IS enforced** by syslog-ng 4.5.0 with `peer-verify(required-trusted)`. A revoked client
  cert is rejected at the TLS handshake (`alert certificate revoked`, OpenSSL alert 44) and its logs are
  dropped; a valid cert is accepted and delivered. Verified both directions.
- The CRL must be **hash-named** `<issuer_subject_hash>.r0` inside the crl-dir (OpenSSL hash-dir lookup).
  `openssl crl -hash -noout -in crl.pem` yields the name.
- syslog-ng **caches the CRL at SSL_CTX init**. Updating the file does NOT take effect on new
  connections until syslog-ng **reloads** (`syslog-ng-ctl reload` / SIGHUP). Verified: a freshly-revoked
  serial is honored only *after* a reload.
- The CRL's `next_update` must be in the future, or OpenSSL rejects it (and would fail every verify).

Conclusion: **CRL hard-revocation is buildable at the syslog-ng layer — no HAProxy front needed** (that
path stays dropped). The reload requirement shapes the distribution mechanism below.

Field-shape and HA are verified later in their own bring-ups (sections 3–4); the SP-2 retention bring-up
already proved syslog-ng routes per-tenant docs into OpenSearch with 0 drops, so those are confirmations
+ targeted hardening, not unknowns.

---

## 1. CRL hard-revoke

### Goal
A revoked device cert must be rejected at the receiver, so a *stolen device key* can no longer inject
logs by connecting directly to port 6514 (bypassing the box deprovision that soft-revoke does today).

### Data flow
```
revoke_device() (API, opngms_app)  ──records serial──▶  revoked_syslog_certs ledger (RLS)
        │ enqueue ARQ job
        ▼
refresh_syslog_crl  (WORKER, owner / RLS-exempt)
   reads ALL revoked serials (owner → every tenant)   reads CA cert+key (owner-only)
   builds a CA-signed CRL                              writes  <volume>/crl/<hash>.r0
        │
        ▼ (shared volume opngms_syslog_certs)
syslog-ng container  ──reload-watcher notices the changed CRL──▶  syslog-ng-ctl reload
   crl-dir("/certs/crl") + peer-verify(required-trusted)  ⇒  revoked cert rejected at TLS
```

### Components
1. **`build_crl()`** — pure helper in `app/services/syslog_ca.py` (mirrors `build_ca`): given the CA
   cert+key PEM and a list of `(serial_int, revocation_date)`, returns a CA-signed CRL PEM with a
   generous `next_update` (default 30 days — survives a worker outage; regenerated daily anyway).
2. **`refresh_syslog_crl(cert_dir)`** — owner-side routine (callable from the worker cron *and* the
   bootstrap CLI). Reads the CA + every ledger serial (owner session), builds the CRL, writes it to
   `cert_dir/crl/`, hash-names it `<hash>.r0` via the `openssl crl -hash` CLI (present in the backend
   image; the bootstrap already shells nothing — we add this one call), atomically replaces the file.
   Idempotent; safe to run when no certs are revoked (emits an empty-but-valid CRL).
3. **Worker wiring** — a daily ARQ cron `refresh_syslog_crl_job` (freshness for `next_update`) **plus**
   an on-revoke enqueue from `revoke_device` so a revocation propagates within seconds, not a day.
   The job no-ops (logs INFO) when the cert volume isn't mounted (core-only deploy) — same
   opt-in-degrades pattern as `purge_log_lake`.
4. **Bootstrap** — `syslog-bootstrap` writes the **initial** CRL alongside CA/server certs, so syslog-ng
   has a valid (possibly empty) CRL at first start and `crl-dir()` doesn't error on an empty dir.
5. **syslog-ng.conf** — add `crl-dir("/certs/crl")` to the `tls()` block of `source s_tls`.
6. **syslog-ng reload-watcher** — a small `deploy/syslog-ng/entrypoint.sh` set as the container command:
   starts `syslog-ng --no-caps -F` and a background poll loop that, when the crl-dir checksum changes,
   runs `syslog-ng-ctl reload`. Polls every 30s (bounded revocation latency). Forwards SIGTERM so
   `docker stop` stays clean. (Rejected alternatives: unconditional timed reload = wasteful; worker
   signalling the syslog-ng container = needs orchestration access, not portable.)
7. **Compose** — the **worker** service gains a `opngms_syslog_certs:/certs` mount **only in
   `docker-compose.logs.yml`** (the log lake is opt-in; the base deploy is unchanged). syslog-ng's mount
   stays read-only (it never writes). The new entrypoint script is mounted like the conf.

### Decisions / caveats
- The CRL is **global** (one CA, one CRL covering all revoked device certs across tenants). Correct: the
  receiver verifies against the single CA.
- Revoke stays box-call-first then ledger-insert (unchanged). With CRL enforcement now at syslog-ng, the
  ledger commit is what matters; the immediate enqueue closes the latency. The earlier "ledger-first"
  idea is unnecessary.
- **Defense-in-depth, not a replacement** for short certs + auto-renew — both stay.
- Latency: revocation takes effect within `enqueue + job runtime + ≤30s watcher poll`. Documented.

---

## 2. Least-privilege `syslog_ca`

### Problem
`opngms_app` gets `GRANT SELECT ON ALL TABLES` (migration 0003), so the encrypted CA **private key**
(`syslog_ca.key_enc`) is readable by the user-facing role. It's Fernet-encrypted (needs `MASTER_KEY`),
so the practical risk is a *read primitive* (SQLi / mass-export bug) exfiltrating the key blob — but it
shouldn't be reachable by a blanket grant at all.

### Approach (chosen): split the key into an owner-only table + a SECURITY DEFINER accessor
- New migration: create table **`syslog_ca_key`** (`id` FK→`syslog_ca.id`, `key_enc bytea`), move the
  existing `key_enc` out of `syslog_ca`, drop `syslog_ca.key_enc`.
- **`REVOKE ALL ON syslog_ca_key FROM opngms_app`** (and rely on no default-privilege re-grant — the
  REVOKE runs after table creation in the same migration). `opngms_app` keeps SELECT on `syslog_ca`
  (the public cert is fine to read).
- A **SECURITY DEFINER** function `opngms_syslog_ca_key()` owned by the DB owner, `GRANT EXECUTE TO
  opngms_app`, returning the `key_enc` bytea. The API signing path (provision/rotate device cert) reads
  the key *only* through this function; it cannot `SELECT` the key table.
- The **worker / bootstrap / rekey** paths read the key directly as owner (no function needed).

### Why this and not the alternatives
- **Owner-session in the API** (give the API an owner engine) was **rejected**: it would put owner DB
  creds in the user-facing process, letting a compromised API bypass RLS entirely — a far bigger hole
  than the one it closes. Invariant #1 forbids user-facing owner queries.
- **Move cert issuance to the worker (async)** is the *fullest* fix (the key never touches `opngms_app`)
  but re-architects synchronous, box-gated provisioning into async jobs with status polling — a UX
  change out of scope for a hardening pass. Tracked as a follow-up.

### Honest bound (documented in the PR)
Because synchronous issuance still needs the key in-process, the function *can* return the (encrypted)
key to `opngms_app`. The win is concrete but bounded: the key is no longer reachable by a **blanket
table grant** — only by invoking one named, single-purpose function. That shrinks the surface from "any
read primitive" to "must call this exact function," which a generic mass-read cannot. Security-reviewed.

### Touch list
`models/syslog_ca.py` (drop key_enc) + new `models/syslog_ca_key.py`; `services/log_forwarding.py`
(`SyslogCaService` key access via function for the API path, direct for owner); `cli.py` bootstrap;
`scripts/rekey_secrets.py` (new table); migration (data move + revoke + function); tests incl. an RLS
test proving `opngms_app` **cannot** `SELECT syslog_ca_key` but **can** call the function.

---

## 3. Field-shape verification

Confirm, at a bring-up, that a log pushed through syslog-ng lands in OpenSearch with the document shape
the search layer expects:
- the per-RDN extraction (`tenant_id` from cert `O=`, `device_id` from `CN=`) populates correctly;
- the indexed doc carries `@timestamp`, `tenant_id`, `device_id`, `host`, `program`, `severity`,
  `message` + the `rfc5424`/dot-nv pairs, with types matching `deploy/opensearch/index-template.json`
  and the fields `app/services/log_search.py` filters/sorts on (esp. `@timestamp` as date,
  `tenant_id`/`device_id` as keyword).

Deliverable: a documented verification run (reuse/extend `scripts/syslog_pipeline_smoke.py`) plus, **only
if the run surfaces a gap**, a targeted index-template mapping hardening (e.g. pin a field to `keyword`
that was dynamically mapped to `text`). No behavior change expected.

## 4. HA multi-node verification

Bring up `docker-compose.logs.multinode.yml` (3-node OpenSearch + `index-template.multinode.json`,
2-shard/1-replica), index docs, kill one node, and confirm the index **stays green/yellow and
searchable** (1 replica tolerates 1 node loss). Deliverable: a documented run + any config fix the run
surfaces (e.g. replica count). No application code change expected.

Sections 3 + 4 ship together with the milestone docs refresh (README + Wiki Log-Lake/Security).

---

## Testing strategy
- **Unit (CI, no infra):** `build_crl` (revoked serial present, valid signature, future `next_update`,
  empty-ledger case); `refresh_syslog_crl` writes a hash-named file + no-ops without the volume;
  `SyslogCaService` key access path; the migration's data move.
- **RLS test:** `opngms_app` cannot `SELECT syslog_ca_key`, can call `opngms_syslog_ca_key()`; CRL
  ledger read as owner spans tenants.
- **Bring-up (manual, documented, not CI):** the CRL reject/accept + reload behavior (already proven in
  the spike — re-run end-to-end through the real worker→volume→syslog-ng path); field-shape; HA.

## PR decomposition
1. **PR1 — least-priv `syslog_ca`** (independent, no infra). Split table + function + migration + tests.
2. **PR2 — CRL hard-revoke** (`build_crl` + `refresh_syslog_crl` + worker cron/on-revoke + bootstrap +
   syslog-ng.conf crl-dir + entrypoint reload-watcher + compose worker mount + tests + bring-up verify).
3. **PR3 — field-shape + HA verification + milestone docs** (bring-up runs, any mapping/config fix,
   README + Wiki).

Each PR: branch off `main`, full local build/test/lint, security-reviewer on PR1+PR2, squash-merge on
green CI. Milestone closes with a version tag + CHANGELOG + README/Wiki refresh.

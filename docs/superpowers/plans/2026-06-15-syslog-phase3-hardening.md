# Syslog Phase 3 hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the four deferred syslog Phase 3 gaps — CRL hard-revoke, least-priv `syslog_ca`,
field-shape + HA verification — without changing user-facing log search behavior.

**Architecture:** Owner-side (worker/bootstrap) generates a CA-signed CRL from the `revoked_syslog_certs`
ledger onto the shared cert volume; syslog-ng enforces it via `crl-dir()` + a reload-watcher. The CA
private key moves to an owner-only table read by the API only through a SECURITY DEFINER function.

**Tech stack:** Python 3.14 / FastAPI / SQLAlchemy 2.0 async / Alembic / ARQ worker; syslog-ng 4.5.0;
OpenSearch 2.17.1; docker-compose.

Spec: `docs/superpowers/specs/2026-06-15-syslog-phase3-hardening-design.md`. Spike findings (CRL DOES
enforce; hash-named `<hash>.r0`; reload required) are in that spec §0.

Current alembic head: **0039** → new migration **0040**.

---

## PR1 — least-privilege `syslog_ca` (branch `feat/syslog-least-priv-ca`)

**Outcome:** `opngms_app` can no longer read the encrypted CA private key via the blanket table grant.
The key lives in an owner-only table; the API signing path reads it only via a SECURITY DEFINER function.

**Files:**
- Modify: `backend/app/models/syslog_ca.py` (drop `key_enc`)
- Create: `backend/app/models/syslog_ca_key.py`
- Modify: `backend/app/models/__init__.py` (register new model)
- Create: `backend/migrations/versions/0040_split_syslog_ca_key.py`
- Modify: `backend/app/services/log_forwarding.py` (`SyslogCaService`: split create/read; key via function for API; `provision_device`/`rotate_device_cert` use `require_ca`)
- Modify: `backend/app/cli.py` (bootstrap: create CA via owner path; read key from new table)
- Modify: `backend/app/scripts/rekey_secrets.py` (rekey `syslog_ca_key.key_enc`, not `syslog_ca.key_enc`)
- Test: `backend/tests/test_syslog_ca_least_priv.py` (new) + update any provisioning fixtures/tests

### Task 1.1: Migration — split table, revoke, accessor function

- [ ] **Step 1: Write the migration** `0040_split_syslog_ca_key.py` (`down_revision = "0039"`):
  - `op.create_table("syslog_ca_key", Column("id", SmallInteger, ForeignKey("syslog_ca.id", ondelete="CASCADE"), primary_key=True), Column("key_enc", LargeBinary, nullable=False))`
  - Move data: `op.execute("INSERT INTO syslog_ca_key (id, key_enc) SELECT id, key_enc FROM syslog_ca WHERE key_enc IS NOT NULL")`
  - `op.drop_column("syslog_ca", "key_enc")`
  - `op.execute("REVOKE ALL ON syslog_ca_key FROM opngms_app")` — undoes the default-privilege grant the just-created table received.
  - Create the accessor (idempotent, hardened search_path):
    ```sql
    CREATE OR REPLACE FUNCTION opngms_syslog_ca_key() RETURNS bytea
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS
    $$ SELECT key_enc FROM public.syslog_ca_key ORDER BY id LIMIT 1 $$;
    ```
  - `op.execute("REVOKE ALL ON FUNCTION opngms_syslog_ca_key() FROM PUBLIC")`
  - `op.execute("GRANT EXECUTE ON FUNCTION opngms_syslog_ca_key() TO opngms_app")`
  - `downgrade()`: drop function; `add_column syslog_ca.key_enc`; move data back; drop `syslog_ca_key`. (Forward-only in practice, but keep it correct.)
- [ ] **Step 2: Run `alembic upgrade head`** against the dev DB; expect 0040 applied, `syslog_ca` has no `key_enc`, `syslog_ca_key` exists, function exists.
- [ ] **Step 3: Commit.**

### Task 1.2: Models

- [ ] **Step 1:** Remove `key_enc` from `SyslogCa`; create `SyslogCaKey` (`id` SmallInteger PK FK→syslog_ca.id, `key_enc` LargeBinary). Register in `models/__init__.py`.
- [ ] **Step 2:** `ruff check app/models` clean; commit.

### Task 1.3: `SyslogCaService` — owner-create vs API-read split (TDD)

- [ ] **Step 1: Write failing tests** `test_syslog_ca_least_priv.py`:
  - `test_app_role_cannot_select_ca_key`: as an **opngms_app** session, `SELECT key_enc FROM syslog_ca_key` raises (insufficient privilege).
  - `test_app_role_can_call_key_function`: as opngms_app, `SELECT opngms_syslog_ca_key()` returns the bytea key.
  - `test_provision_signs_via_function`: a provisioning flow (CA pre-seeded as owner) issues a device cert successfully as opngms_app (signs using the function-fetched key).
  - `test_require_ca_raises_when_absent`: `require_ca()` raises a clear error when no CA row exists.
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** in `log_forwarding.py`:
  - `SyslogCaService.get()` selects `SyslogCa` (now no key column).
  - `require_ca()` → returns the CA or raises `RuntimeError("syslog CA not initialized — run syslog-bootstrap")`.
  - `ensure_ca()` → **owner-only create** (build_ca, insert `SyslogCa` + `SyslogCaKey`). Used by bootstrap/worker. (Keep its signature; it now writes both rows.)
  - `async _ca_key_enc()` → `(await session.execute(text("SELECT opngms_syslog_ca_key()"))).scalar_one()` (works for both roles).
  - `device_cert(ca, *, tenant_id, device_id)` → `issue_device_cert(ca.cert_pem.encode(), crypto.decrypt_bytes(await self._ca_key_enc()), …)` (make it async).
  - `provision_device` / `rotate_device_cert`: replace `svc.ensure_ca()` with `svc.require_ca()`; await the now-async `device_cert`.
- [ ] **Step 4: Run tests, verify pass.**
- [ ] **Step 5: Commit.**

### Task 1.4: bootstrap + rekey + fixtures

- [ ] **Step 1:** `cli.py run_syslog_bootstrap`: still owner; `svc.ensure_ca()` now writes both rows; read the server-cert signing key via the new table (`SyslogCaKey`) directly (owner) or `_ca_key_enc()`.
- [ ] **Step 2:** `rekey_secrets.py`: change the syslog CA rekey to `SELECT id, key_enc FROM syslog_ca_key` / `UPDATE syslog_ca_key …`. Keep the metadata guard test green.
- [ ] **Step 3:** Seed the CA as owner in any provisioning test fixture that previously relied on inline `ensure_ca` under opngms_app. Update `tests/test_system_api.py` active-settings count ONLY if touched (it isn't here).
- [ ] **Step 4: Run full `pytest -q`**, `ruff check app/`. Green. Commit.

### Task 1.5: Reviews + PR
- [ ] Spec-compliance + code-quality + **security-reviewer** (RLS/least-priv/crypto). Fix BLOCKER/IMPORTANT.
- [ ] Push, open PR, green CI, squash-merge.

---

## PR2 — CRL hard-revoke (branch `feat/syslog-crl-hard-revoke`)

**Outcome:** a revoked device cert is rejected at the syslog-ng receiver (verified end-to-end at a
bring-up). Depends on PR1's owner-only key (the worker reads it owner-side).

**Files:**
- Modify: `backend/app/services/syslog_ca.py` (`build_crl`)
- Create: `backend/app/services/syslog_crl.py` (`refresh_syslog_crl(cert_dir)`, hash-naming, atomic write, no-op-without-volume)
- Modify: `backend/app/worker.py` (cron `refresh_syslog_crl_job` + enqueue helper) + `WorkerSettings`
- Modify: `backend/app/services/log_forwarding.py` (`revoke_device` enqueues the refresh) — or enqueue in the API route after commit (decide: route-level enqueue keeps the service infra-free → prefer enqueue in `revoke_log_forwarding` after `session.commit()`).
- Modify: `backend/app/cli.py` (bootstrap writes the initial CRL via `refresh_syslog_crl`)
- Modify: `deploy/syslog-ng/syslog-ng.conf` (`crl-dir("/certs/crl")` in `tls()`)
- Create: `deploy/syslog-ng/entrypoint.sh` (start syslog-ng + checksum-gated `syslog-ng-ctl reload` poll loop, SIGTERM-forwarding)
- Modify: `docker-compose.logs.yml` (syslog-ng `command`→entrypoint + mount the script; **worker** gains `opngms_syslog_certs:/certs` rw) and `docker-compose.logs.multinode.yml` if it overrides syslog-ng
- Test: `backend/tests/test_syslog_crl.py`

### Task 2.1: `build_crl` (TDD, pure)
- [ ] Tests: a CRL signed by the CA lists a given serial (revoked), validates against the CA, `next_update` is in the future; empty-ledger → valid empty CRL. (`openssl crl -CAfile` or `cryptography` verify.)
- [ ] Implement `build_crl(ca_cert_pem, ca_key_pem, revoked: list[tuple[int, datetime]], *, next_update_days=30) -> bytes`. Commit.

### Task 2.2: `refresh_syslog_crl` (TDD)
- [ ] Tests: writes `<cert_dir>/crl/<hash>.r0` (hash from `openssl crl -hash`); content revokes the ledger serials (owner read across tenants); **no-op + INFO** when `cert_dir` is unset/missing; atomic replace (temp + rename). Stub the ledger with two tenants' revoked serials.
- [ ] Implement reading the CA (cert via ORM, key via `SyslogCaKey` owner) + all `revoked_syslog_certs.serial` (hex→int), call `build_crl`, hash-name, write atomically. Verify `openssl` is in the backend image (`Dockerfile`); if absent, add it. Commit.

### Task 2.3: worker cron + on-revoke enqueue
- [ ] Add `refresh_syslog_crl_job` to the worker (daily cron, owner session, reads `settings.syslog_cert_dir` — add a setting defaulting to `/certs`, the mount path); register in `WorkerSettings.cron_jobs`. No-ops without the volume.
- [ ] After a successful revoke, enqueue the job (`enqueue_job("refresh_syslog_crl_job")`) from the API route post-commit (best-effort; log on failure). Test the enqueue is attempted. Commit.

### Task 2.4: bootstrap + syslog-ng.conf + entrypoint + compose
- [ ] bootstrap calls `refresh_syslog_crl(cert_dir)` after writing certs (initial CRL, possibly empty).
- [ ] `syslog-ng.conf`: add `crl-dir("/certs/crl")` to the `tls()` of `s_tls` (comment: requires hash-named CRL + reload-watcher; verified enforcing on 4.5.0 2026-06-15).
- [ ] `entrypoint.sh`: `syslog-ng --no-caps -F &`, trap TERM→`syslog-ng-ctl stop`, poll `sha256sum /certs/crl/*.r0` every 30s, on change `syslog-ng-ctl reload`. Make executable.
- [ ] compose: syslog-ng `command: ["/entrypoint.sh"]` + mount `./deploy/syslog-ng/entrypoint.sh:/entrypoint.sh:ro`; add worker `volumes: [opngms_syslog_certs:/certs]`. Mirror in multinode overlay if it redefines syslog-ng.
- [ ] Commit.

### Task 2.5: bring-up verification (documented, not CI)
- [ ] Bring up `prod + logs` overlay locally (or a trimmed stack): enable forwarding for a fake device, push a log (accepted), revoke it → worker writes CRL → syslog-ng reloads → the same cert is now rejected (TLS alert) and a fresh valid cert still works. Capture evidence in the PR description.

### Task 2.6: reviews + PR (incl. security-reviewer: CRL signing, owner key custody, volume perms).

---

## PR3 — field-shape + HA verification + milestone docs (branch `chore/syslog-phase3-verify-docs`)

- [ ] **Field-shape:** bring-up; push a log through syslog-ng; GET the indexed doc; confirm `@timestamp`
  (date), `tenant_id`/`device_id` (keyword), `severity`, `message`, rfc5424/dot-nv fields match
  `deploy/opensearch/index-template.json` + `app/services/log_search.py` expectations. Harden the
  mapping ONLY if a gap appears. Document.
- [ ] **HA:** bring up `docker-compose.logs.multinode.yml`; index docs; kill one node; confirm the index
  stays green/yellow + searchable. Fix `index-template.multinode.json` only if needed. Document.
- [ ] **Docs:** README (Features/Project-status note for CRL hard-revoke + least-priv) + Wiki
  (Log-Lake: per-tenant CRL + reload model; Security: CA-key least-priv + revocation enforcement).
- [ ] Reviews + PR.

---

## Milestone close
- [ ] Tag a version (minor) + CHANGELOG section + README/Wiki refresh (per [[keep-readme-updated]]).
- [ ] Update memory: `syslog-phase3-deferred-backlog` (CRL/field-shape/HA/least-priv → DONE), resume note.

## Self-review notes
- PR1 ordering before PR2 is required (PR2's worker reads the key via the owner-only path PR1 creates).
- The only UX change: the API no longer creates the CA (bootstrap/worker do). Dev/test must seed it as
  owner. Documented in PR1.
- `openssl` CLI dependency for hash-naming: verify present in the backend image (Task 2.2), else add.

# Syslog Pipeline — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the push-based log pipeline foundation — an internal CA, per-device mTLS provisioning into OPNsense, and an opt-in syslog-ng→OpenSearch stack — so device logs land in OpenSearch attributed to `{tenant_id, device_id}`. No search UI (Phase 2).

**Architecture:** Backend CA (key encrypted at rest) issues a receiver server cert + per-device client certs (`CN=device_id, O=tenant_id`). A provisioning API issues a device cert, imports the CA + cert into the box (`trust/*` API) and configures a `tls4` syslog destination (connector). A config-only syslog-ng receiver (opt-in compose) terminates mTLS, derives tenant/device from the verified peer cert, parses filterlog + Suricata EVE, and writes to OpenSearch.

**Tech Stack:** Python 3.14 · `cryptography` x509 · FastAPI · SQLAlchemy 2 async + RLS · OPNsense `trust`/`syslog` API · syslog-ng (config-only) · OpenSearch 2.x + ISM · Docker Compose · pytest + respx.

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-pipeline-phase1-design.md`
**Branch:** `feat/syslog-pipeline-phase1` (already created).

**Box facts (verified read-only, OPNsense 26.1.9):** syslog destination fields = `enabled, transport, program, level, facility, hostname, certificate, port, rfc5424, description`; `transport` includes `tls4`/`tls6`; `certificate` selects the client cert (mTLS). `trust/cert` import = `{cert:{action:"import", descr, crt_payload, prv_payload}}`; `trust/ca` import = `{ca:{action:"existing", descr, crt_payload}}`. Routes `syslog/settings/addDestination` + `syslog/service/reconfigure` return 200.

---

## File Structure

**Backend — create:**
- `app/services/syslog_ca.py` — pure x509 CA primitives (build CA, issue server/device certs).
- `app/services/log_forwarding.py` — `SyslogCaService` (DB-backed CA, Fernet) + `provision_device` / `deprovision_device`.
- `app/models/syslog_ca.py` — `SyslogCa` singleton.
- `app/models/device_log_forwarding.py` — `DeviceLogForwarding` (tenant-scoped, RLS).
- `app/repositories/device_log_forwarding.py` — repo.
- `app/schemas/log_forwarding.py` — `LogForwardingOut`.
- `app/api/log_forwarding.py` — enable/disable/status endpoints.
- `migrations/versions/0024_log_forwarding.py`.
- `deploy/syslog-ng/syslog-ng.conf` — the receiver config.
- `deploy/opensearch/index-template.json`, `deploy/opensearch/ism-policy.json` — index mapping + retention.
- `docker-compose.logs.yml` — opt-in opensearch + syslog-ng + bootstrap.
- `scripts/syslog_pipeline_smoke.py` — scripted mTLS→OpenSearch integration check.

**Backend — modify:**
- `app/core/rls.py` — add `device_log_forwarding` to `TENANT_TABLES`.
- `app/models/__init__.py` — register the two models.
- `app/connectors/opnsense/client.py` — `import_ca`, `import_cert`, `add_syslog_destination`, `delete_syslog_destination`, `delete_cert`, `reconfigure_syslog`.
- `app/core/config.py` — syslog/opensearch settings.
- `app/main.py` — include the router.
- `app/cli.py` — a `syslog-bootstrap` command (writes receiver certs + applies the OpenSearch template/ISM).
- `.env.example`, `README.md`.

---

## Conventions (read once)
- Backend DB tests: from `backend/`, prefix with `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`.
- `cryptography` is already a dependency (Fernet lives in it). `app.core.crypto.encrypt_bytes/decrypt_bytes` (Fernet) encrypt the CA key.
- The connector's single HTTP boundary is `OpnsenseClient._post(path, payload)` / `_get(path)` (see `apply_alias`). Mirror its shape; respx is used to test connector methods (see `tests/test_connector_*`).
- English everywhere; commit after each task's tests pass.

---

# PHASE A — CA + data model

## Task A1: Models + migration 0024 + RLS

**Files:**
- Create: `app/models/syslog_ca.py`, `app/models/device_log_forwarding.py`, `migrations/versions/0024_log_forwarding.py`
- Modify: `app/models/__init__.py`, `app/core/rls.py`
- Test: `tests/test_log_forwarding_models.py`, `tests/test_migration_0024.py`

- [ ] **Step 1: Write the failing model test**

```python
# backend/tests/test_log_forwarding_models.py
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SINGLETON_ID, SyslogCa


async def test_syslog_ca_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(SyslogCa(id=SINGLETON_ID, cert_pem="-----CA-----", key_enc=b"enc"))
        await s.commit()
        row = (await s.execute(select(SyslogCa))).scalar_one()
        assert row.id == SINGLETON_ID and row.key_enc == b"enc"


async def test_device_log_forwarding_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(DeviceLogForwarding(device_id=did, tenant_id=tid, enabled=True, cert_serial="01",
                                  cert_fingerprint="ab", opnsense_cert_uuid="u1", opnsense_dest_uuid="u2"))
        await s.commit()
        row = (await s.execute(select(DeviceLogForwarding))).scalar_one()
        assert row.enabled is True and row.opnsense_dest_uuid == "u2"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_forwarding_models.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement the models**

```python
# backend/app/models/syslog_ca.py
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, LargeBinary, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SINGLETON_ID = 1


class SyslogCa(Base):
    """Global (non-tenant) internal CA for the log pipeline — one row (id=1). Key encrypted at rest."""

    __tablename__ = "syslog_ca"
    __table_args__ = (CheckConstraint("id = 1", name="ck_syslog_ca_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    cert_pem: Mapped[str] = mapped_column(Text)             # CA public cert PEM
    key_enc: Mapped[bytes] = mapped_column(LargeBinary)     # Fernet-encrypted CA private key PEM
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

```python
# backend/app/models/device_log_forwarding.py
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeviceLogForwarding(Base):
    """Per-device log-forwarding provisioning state (tenant-scoped, RLS)."""

    __tablename__ = "device_log_forwarding"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    cert_serial: Mapped[str] = mapped_column(String, default="", server_default="")
    cert_fingerprint: Mapped[str] = mapped_column(String, default="", server_default="")
    opnsense_ca_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    opnsense_cert_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    opnsense_dest_uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    provisioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Register + RLS**

`app/models/__init__.py` — add (match the file's style + `__all__` if present):
```python
from app.models.device_log_forwarding import DeviceLogForwarding  # noqa: F401
from app.models.syslog_ca import SyslogCa  # noqa: F401
```
`app/core/rls.py` — append `"device_log_forwarding"` to the END of `TENANT_TABLES` (only the tenant-scoped table; `syslog_ca` is global, NO RLS).

- [ ] **Step 5: Write the migration**

```python
# backend/migrations/versions/0024_log_forwarding.py
"""syslog_ca (global) + device_log_forwarding (tenant-scoped, RLS)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "syslog_ca",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("cert_pem", sa.Text(), nullable=False),
        sa.Column("key_enc", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_syslog_ca_singleton"),
    )
    op.create_table(
        "device_log_forwarding",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("cert_serial", sa.String(), nullable=False, server_default=""),
        sa.Column("cert_fingerprint", sa.String(), nullable=False, server_default=""),
        sa.Column("opnsense_ca_uuid", sa.String(), nullable=True),
        sa.Column("opnsense_cert_uuid", sa.String(), nullable=True),
        sa.Column("opnsense_dest_uuid", sa.String(), nullable=True),
        sa.Column("provisioned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id"),
    )
    op.execute("ALTER TABLE device_log_forwarding ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_log_forwarding FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("device_log_forwarding"))
    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON device_log_forwarding FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON device_log_forwarding")
    op.execute("ALTER TABLE device_log_forwarding NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_log_forwarding DISABLE ROW LEVEL SECURITY")
    op.drop_table("device_log_forwarding")
    op.drop_table("syslog_ca")
```

- [ ] **Step 6: Migration test + scratch-DB verify**

Write `tests/test_migration_0024.py` asserting both tables + `device_log_forwarding` RLS forced (mirror `tests/test_migration_0023.py`):
```python
from sqlalchemy import text


async def test_migration_0024(db_engine):
    async with db_engine.begin() as conn:
        tabs = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('syslog_ca','device_log_forwarding')"
        ))).scalars().all()
        assert set(tabs) == {"syslog_ca", "device_log_forwarding"}
        rls = (await conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='device_log_forwarding'"
        ))).one()
        assert rls == (True, True)
```
Then verify on a scratch DB (mirror Task A1 of the reliability plan): create `opngms_mig0024`, `ALEMBIC_DATABASE_URL=…opngms_mig0024 alembic upgrade head` (expect `0023 -> 0024`), downgrade -1, upgrade head, drop the scratch DB. Then run both new tests (with `TEST_DATABASE_URL`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/syslog_ca.py backend/app/models/device_log_forwarding.py backend/app/models/__init__.py backend/app/core/rls.py backend/migrations/versions/0024_log_forwarding.py backend/tests/test_log_forwarding_models.py backend/tests/test_migration_0024.py
git commit -m "feat(syslog): log-forwarding models + migration 0024 (CA singleton + RLS state)"
```

---

## Task A2: CA primitives (pure x509)

**Files:**
- Create: `app/services/syslog_ca.py`
- Test: `tests/test_syslog_ca.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_syslog_ca.py
from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.services.syslog_ca import build_ca, issue_device_cert, issue_server_cert


def test_build_ca_is_a_ca():
    cert_pem, key_pem = build_ca()
    cert = x509.load_pem_x509_certificate(cert_pem)
    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert bc.ca is True
    assert cert.subject.rfc4514_string().endswith("OPNGMS Syslog CA")


def test_issue_device_cert_subject_and_chain():
    ca_cert_pem, ca_key_pem = build_ca()
    cert_pem, key_pem = issue_device_cert(ca_cert_pem, ca_key_pem,
                                          tenant_id="tenant-1", device_id="device-9")
    cert = x509.load_pem_x509_certificate(cert_pem)
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
    o = cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)[0].value
    assert cn == "device-9"
    assert o == "tenant-1"
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
    # chains to the CA: issuer == CA subject, and the CA public key verifies the signature
    ca = x509.load_pem_x509_certificate(ca_cert_pem)
    assert cert.issuer == ca.subject
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                           __import__("cryptography.hazmat.primitives.asymmetric.padding", fromlist=["PKCS1v15"]).PKCS1v15(),
                           cert.signature_hash_algorithm)


def test_issue_server_cert_has_san_and_server_eku():
    ca_cert_pem, ca_key_pem = build_ca()
    cert_pem, _ = issue_server_cert(ca_cert_pem, ca_key_pem, hostname="logs.opngms.example")
    cert = x509.load_pem_x509_certificate(cert_pem)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "logs.opngms.example" in san.get_values_for_type(x509.DNSName)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_syslog_ca.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

```python
# backend/app/services/syslog_ca.py
"""Pure x509 CA primitives for the log pipeline (no DB). Built on `cryptography`."""
from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_CN = "OPNGMS Syslog CA"


def _gen_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def build_ca() -> tuple[bytes, bytes]:
    """Generate a self-signed CA. Returns (ca_cert_pem, ca_key_pem)."""
    key = _gen_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_CN)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                          data_encipherment=False, key_agreement=False, key_cert_sign=True,
                          crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
        .sign(key, hashes.SHA256())
    )
    return _cert_pem(cert), _key_pem(key)


def _load(ca_cert_pem: bytes, ca_key_pem: bytes):
    return (x509.load_pem_x509_certificate(ca_cert_pem),
            serialization.load_pem_private_key(ca_key_pem, password=None))


def _issue(ca_cert_pem, ca_key_pem, *, subject, sans, eku, days) -> tuple[bytes, bytes]:
    ca_cert, ca_key = _load(ca_cert_pem, ca_key_pem)
    key = _gen_key()
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(ca_cert.subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage(eku), critical=False)
    )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    cert = builder.sign(ca_key, hashes.SHA256())
    return _cert_pem(cert), _key_pem(key)


def issue_device_cert(ca_cert_pem, ca_key_pem, *, tenant_id: str, device_id: str) -> tuple[bytes, bytes]:
    """Per-device CLIENT cert: subject CN=<device_id>, O=<tenant_id>."""
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, device_id),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, tenant_id),
    ])
    return _issue(ca_cert_pem, ca_key_pem, subject=subject, sans=None,
                  eku=[ExtendedKeyUsageOID.CLIENT_AUTH], days=730)


def issue_server_cert(ca_cert_pem, ca_key_pem, *, hostname: str) -> tuple[bytes, bytes]:
    """Receiver SERVER cert. SAN = hostname (DNS) or IP; SERVER_AUTH EKU."""
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    try:
        san = [x509.IPAddress(ipaddress.ip_address(hostname))]
    except ValueError:
        san = [x509.DNSName(hostname)]
    return _issue(ca_cert_pem, ca_key_pem, subject=subject, sans=san,
                  eku=[ExtendedKeyUsageOID.SERVER_AUTH], days=730)


def cert_serial_and_fingerprint(cert_pem: bytes) -> tuple[str, str]:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return format(cert.serial_number, "x"), cert.fingerprint(hashes.SHA256()).hex()
```
(The test's signature-verify line uses `padding.PKCS1v15()`; the helper `padding` is imported.)

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_syslog_ca.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/syslog_ca.py backend/tests/test_syslog_ca.py
git commit -m "feat(syslog): x509 CA primitives (server + per-device client certs)"
```

---

# PHASE B — Connector + provisioning

## Task B1: Connector trust + syslog methods

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Test: `tests/test_connector_log_forwarding.py`

- [ ] **Step 1: Write the failing tests** (respx — mirror `tests/test_connector_apply_alias.py` for the client + respx setup)

```python
# backend/tests/test_connector_log_forwarding.py
import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient

BASE = "https://fw.test"


def _client():
    return OpnsenseClient(BASE, "k", "s", verify_tls=False)


@respx.mock
async def test_import_ca_posts_existing_action():
    route = respx.post(f"{BASE}/api/trust/ca/add").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "ca-uuid"}))
    uuid_ = await _client().import_ca("-----CA PEM-----", descr="OPNGMS CA")
    assert uuid_ == "ca-uuid"
    body = route.calls[0].request.read().decode()
    assert '"action": "existing"' in body or '"action":"existing"' in body


@respx.mock
async def test_import_cert_posts_import_with_key():
    respx.post(f"{BASE}/api/trust/cert/add").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "cert-uuid"}))
    uuid_ = await _client().import_cert("-----CERT-----", "-----KEY-----", descr="dev-9")
    assert uuid_ == "cert-uuid"


@respx.mock
async def test_add_syslog_destination_then_reconfigure():
    add = respx.post(f"{BASE}/api/syslog/settings/addDestination").mock(
        return_value=httpx.Response(200, json={"result": "saved", "uuid": "dest-uuid"}))
    rec = respx.post(f"{BASE}/api/syslog/service/reconfigure").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    uuid_ = await _client().add_syslog_destination(
        hostname="logs.example", port=6514, certificate_uuid="cert-uuid")
    assert uuid_ == "dest-uuid"
    assert add.called and rec.called
    body = add.calls[0].request.read().decode()
    assert '"transport": "tls4"' in body or '"transport":"tls4"' in body
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_connector_log_forwarding.py -v`
Expected: FAIL (methods missing).

- [ ] **Step 3: Implement (append methods to `OpnsenseClient` in `app/connectors/opnsense/client.py`)**

READ the class first to reuse its `_post`/`_get` helpers and constants (`RECONFIGURE_TIMEOUT`). Add:
```python
    async def import_ca(self, ca_cert_pem: str, *, descr: str) -> str:
        """Import a CA public cert into the box's trust store (so it trusts the receiver). Returns uuid."""
        res = await self._post("trust/ca/add",
                               {"ca": {"action": "existing", "descr": descr, "crt_payload": ca_cert_pem}})
        return res.get("uuid", "")

    async def import_cert(self, cert_pem: str, key_pem: str, *, descr: str) -> str:
        """Import a client cert + key into the box's trust store (the syslog client cert). Returns uuid."""
        res = await self._post("trust/cert/add",
                               {"cert": {"action": "import", "descr": descr,
                                         "crt_payload": cert_pem, "prv_payload": key_pem}})
        return res.get("uuid", "")

    async def add_syslog_destination(self, *, hostname: str, port: int, certificate_uuid: str,
                                     description: str = "OPNGMS log forwarding") -> str:
        """Add a TLS (mTLS) remote-syslog destination presenting `certificate_uuid`; reconfigure. Returns uuid."""
        res = await self._post("syslog/settings/addDestination", {"destination": {
            "enabled": "1", "transport": "tls4", "program": "", "level": "", "facility": "",
            "hostname": hostname, "certificate": certificate_uuid, "port": str(port),
            "rfc5424": "1", "description": description}})
        uuid_ = res.get("uuid", "")
        await self._post("syslog/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return uuid_

    async def delete_syslog_destination(self, dest_uuid: str) -> dict:
        res = await self._post(f"syslog/settings/delDestination/{dest_uuid}", {})
        await self._post("syslog/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return res

    async def delete_cert(self, cert_uuid: str) -> dict:
        return await self._post(f"trust/cert/del/{cert_uuid}", {})
```
(If `_post`'s signature differs (e.g. no `timeout` kwarg), match the existing `apply_alias` reconfigure call exactly.)

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_connector_log_forwarding.py -v`
Expected: PASS. `.venv/bin/ruff check app/connectors/opnsense/client.py` clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/opnsense/client.py backend/tests/test_connector_log_forwarding.py
git commit -m "feat(syslog): connector trust-cert import + mTLS syslog destination"
```

---

## Task B2: CA service + provisioning + API

**Files:**
- Create: `app/services/log_forwarding.py`, `app/repositories/device_log_forwarding.py`, `app/schemas/log_forwarding.py`, `app/api/log_forwarding.py`
- Modify: `app/main.py`, `app/core/config.py`
- Test: `tests/test_log_forwarding_service.py`, `tests/test_log_forwarding_api.py`

- [ ] **Step 1: Settings** — add to `Settings` (`app/core/config.py`):
```python
    syslog_receiver_host: str = "logs.opngms.local"   # public name/IP devices ship logs to
    syslog_tls_port: int = 6514
```

- [ ] **Step 2: Write the failing service test**

```python
# backend/tests/test_log_forwarding_service.py
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SyslogCa
from app.services.log_forwarding import SyslogCaService, provision_device


class FakeClient:
    def __init__(self):
        self.calls = []

    async def import_ca(self, pem, *, descr):
        self.calls.append("import_ca"); return "ca-uuid"

    async def import_cert(self, cert, key, *, descr):
        self.calls.append("import_cert"); return "cert-uuid"

    async def add_syslog_destination(self, *, hostname, port, certificate_uuid, description="x"):
        self.calls.append(("dest", hostname, port, certificate_uuid)); return "dest-uuid"


async def test_ensure_ca_is_idempotent(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SyslogCaService(s)
        a = await svc.ensure_ca(); await s.commit()
        b = await svc.ensure_ca(); await s.commit()
        assert a.cert_pem == b.cert_pem
        assert (await s.execute(select(SyslogCa))).scalars().all().__len__() == 1


async def test_provision_device_issues_imports_and_configures(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        client = FakeClient()
        row = await provision_device(s, tenant_id=tid, device_id=did, client=client,
                                     receiver_host="logs.example", receiver_port=6514)
        await s.commit()
        assert row.enabled is True
        assert row.opnsense_dest_uuid == "dest-uuid"
        assert client.calls[0] == "import_ca" and client.calls[1] == "import_cert"
        assert (await s.get(DeviceLogForwarding, did)).cert_serial != ""
```

- [ ] **Step 3: Run to verify it fails** (ModuleNotFoundError).

- [ ] **Step 4: Implement the service**

```python
# backend/app/services/log_forwarding.py
"""DB-backed CA (key encrypted at rest) + per-device provisioning orchestration."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SINGLETON_ID, SyslogCa
from app.services.syslog_ca import (
    build_ca,
    cert_serial_and_fingerprint,
    issue_device_cert,
)


class SyslogCaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SyslogCa | None:
        return (await self.session.execute(select(SyslogCa))).scalar_one_or_none()

    async def ensure_ca(self) -> SyslogCa:
        row = await self.get()
        if row is not None:
            return row
        cert_pem, key_pem = build_ca()
        row = SyslogCa(id=SINGLETON_ID, cert_pem=cert_pem.decode(), key_enc=crypto.encrypt_bytes(key_pem))
        self.session.add(row)
        await self.session.flush()
        return row

    def device_cert(self, ca: SyslogCa, *, tenant_id: uuid.UUID, device_id: uuid.UUID) -> tuple[bytes, bytes]:
        return issue_device_cert(ca.cert_pem.encode(), crypto.decrypt_bytes(bytes(ca.key_enc)),
                                 tenant_id=str(tenant_id), device_id=str(device_id))


async def provision_device(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                           client, receiver_host: str, receiver_port: int) -> DeviceLogForwarding:
    """Issue a device cert, import the CA + cert into the box, configure the mTLS syslog destination,
    and record state. `client` is an OpnsenseClient (or a stub with the same methods)."""
    svc = SyslogCaService(session)
    ca = await svc.ensure_ca()
    cert_pem, key_pem = svc.device_cert(ca, tenant_id=tenant_id, device_id=device_id)
    serial, fp = cert_serial_and_fingerprint(cert_pem)
    ca_uuid = await client.import_ca(ca.cert_pem, descr="OPNGMS Syslog CA")
    cert_uuid = await client.import_cert(cert_pem.decode(), key_pem.decode(), descr=f"opngms-logs {device_id}")
    dest_uuid = await client.add_syslog_destination(
        hostname=receiver_host, port=receiver_port, certificate_uuid=cert_uuid)
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None:
        row = DeviceLogForwarding(device_id=device_id, tenant_id=tenant_id)
        session.add(row)
    row.enabled = True
    row.tenant_id = tenant_id
    row.cert_serial, row.cert_fingerprint = serial, fp
    row.opnsense_ca_uuid, row.opnsense_cert_uuid, row.opnsense_dest_uuid = ca_uuid, cert_uuid, dest_uuid
    row.provisioned_at = datetime.now(UTC)
    await session.flush()
    return row


async def deprovision_device(session: AsyncSession, *, device_id: uuid.UUID, client) -> bool:
    """Remove the syslog destination + client cert from the box and mark disabled. Idempotent."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None:
        return False
    if row.opnsense_dest_uuid:
        await client.delete_syslog_destination(row.opnsense_dest_uuid)
    if row.opnsense_cert_uuid:
        await client.delete_cert(row.opnsense_cert_uuid)
    row.enabled = False
    row.opnsense_dest_uuid = None
    await session.flush()
    return True
```

- [ ] **Step 5: Run the service test → PASS.**

- [ ] **Step 6: Repository + schema + API**

`app/repositories/device_log_forwarding.py`:
```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_log_forwarding import DeviceLogForwarding


class DeviceLogForwardingRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def get(self, device_id: uuid.UUID) -> DeviceLogForwarding | None:
        return (await self.session.execute(
            select(DeviceLogForwarding).where(
                DeviceLogForwarding.tenant_id == self.tenant_id,
                DeviceLogForwarding.device_id == device_id)
        )).scalar_one_or_none()
```

`app/schemas/log_forwarding.py`:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class LogForwardingOut(BaseModel):
    device_id: uuid.UUID
    enabled: bool
    cert_serial: str
    cert_fingerprint: str
    provisioned_at: datetime | None
```

`app/api/log_forwarding.py` (mirror `app/api/settings.py` for the device-load + connector-build pattern):
```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.device_log_forwarding import DeviceLogForwardingRepository
from app.schemas.log_forwarding import LogForwardingOut
from app.services.audit import AuditService
from app.services.log_forwarding import deprovision_device, provision_device

router = APIRouter(prefix="/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding",
                   tags=["log-forwarding"])


def _client(device: Device) -> OpnsenseClient:
    return OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
                          crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
                          tls_fingerprint=device.tls_fingerprint)


def _out(row) -> LogForwardingOut:
    if row is None:
        return LogForwardingOut(device_id=uuid.UUID(int=0), enabled=False, cert_serial="",
                                cert_fingerprint="", provisioned_at=None)
    return LogForwardingOut(device_id=row.device_id, enabled=row.enabled, cert_serial=row.cert_serial,
                            cert_fingerprint=row.cert_fingerprint, provisioned_at=row.provisioned_at)


async def _device(session, tenant_id, device_id) -> Device:
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


@router.get("", response_model=LogForwardingOut)
async def status_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    await _device(session, tenant_id, device_id)
    return _out(await DeviceLogForwardingRepository(session, tenant_id).get(device_id))


@router.post("/enable", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def enable_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    s = get_settings()
    try:
        row = await provision_device(session, tenant_id=tenant_id, device_id=device_id,
                                     client=_client(device), receiver_host=s.syslog_receiver_host,
                                     receiver_port=s.syslog_tls_port)
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.enable",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={"serial": row.cert_serial})
    out = _out(row)
    await session.commit()
    return out


@router.post("/disable", response_model=LogForwardingOut, dependencies=[Depends(enforce_csrf)])
async def disable_log_forwarding(
    tenant_id: uuid.UUID, device_id: uuid.UUID, request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> LogForwardingOut:
    device = await _device(session, tenant_id, device_id)
    try:
        await deprovision_device(session, device_id=device_id, client=_client(device))
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="log_forwarding.disable",
        target_type="device", target_id=str(device_id),
        ip=request.client.host if request.client else None, details={})
    out = _out(await DeviceLogForwardingRepository(session, tenant_id).get(device_id))
    await session.commit()
    return out
```
Mount the router in `app/main.py` (`from app.api.log_forwarding import router as log_forwarding_router` + `app.include_router(log_forwarding_router)`).

- [ ] **Step 7: Write the API test** (mirror `tests/test_settings_api.py` / the device-scoped RBAC tests; monkeypatch the connector so no real box is hit)

```python
# backend/tests/test_log_forwarding_api.py
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


class FakeClient:
    async def import_ca(self, pem, *, descr): return "ca"
    async def import_cert(self, c, k, *, descr): return "cert"
    async def add_syslog_destination(self, *, hostname, port, certificate_uuid, description="x"): return "dest"
    async def delete_syslog_destination(self, u): return {}
    async def delete_cert(self, u): return {}


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _login(api_client, email): 
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"}); assert r.status_code == 200


async def test_enable_then_status(api_client, db_engine, monkeypatch):
    import app.api.log_forwarding as mod
    monkeypatch.setattr(mod, "_client", lambda device: FakeClient())
    tid, did = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/enable", headers=csrf_headers(api_client))
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is True and r.json()["cert_serial"]
    g = await api_client.get(f"/api/tenants/{tid}/devices/{did}/log-forwarding")
    assert g.json()["enabled"] is True


async def test_read_only_denied(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/devices/{did}/log-forwarding/enable", headers=csrf_headers(api_client))
    assert r.status_code == 403
```

- [ ] **Step 8: Run all → PASS**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_forwarding_service.py tests/test_log_forwarding_api.py -v`
Expected: PASS. `.venv/bin/ruff check app/services/log_forwarding.py app/api/log_forwarding.py` clean.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/log_forwarding.py backend/app/repositories/device_log_forwarding.py backend/app/schemas/log_forwarding.py backend/app/api/log_forwarding.py backend/app/main.py backend/app/core/config.py backend/tests/test_log_forwarding_service.py backend/tests/test_log_forwarding_api.py
git commit -m "feat(syslog): CA service + per-device provisioning API (enable/disable/status)"
```

- [ ] **Step 10: Live revertible box verify (manual, documented)**

Against the real box (192.168.1.82, consented test box), with a temporary `.env` pointing the connector at it, call `enable` then confirm via the OPNsense API that a CA + client cert appear in the trust store and a `tls4` syslog destination references the cert; then call `disable` and confirm cleanup. Record the result in the commit/PR. (No code; this validates the connector payloads on real OPNsense.)

---

# PHASE C — Infra (config + scripted integration)

## Task C1: OpenSearch index template + ISM + bootstrap CLI

**Files:**
- Create: `deploy/opensearch/index-template.json`, `deploy/opensearch/ism-policy.json`
- Modify: `app/cli.py` (add `syslog-bootstrap`)
- Modify: `app/core/config.py` (opensearch settings + `log_retention_days`)

- [ ] **Step 1: Settings** — add:
```python
    opensearch_url: str = "http://opensearch:9200"
    opensearch_user: str = "admin"
    opensearch_password: str = ""
    log_retention_days: int = 30
```

- [ ] **Step 2: Index template** — `deploy/opensearch/index-template.json`:
```json
{
  "index_patterns": ["opngms-logs-*"],
  "template": {
    "settings": { "number_of_shards": 1, "number_of_replicas": 0, "plugins.index_state_management.policy_id": "opngms-logs-retention" },
    "mappings": { "properties": {
      "@timestamp": { "type": "date" },
      "tenant_id": { "type": "keyword" },
      "device_id": { "type": "keyword" },
      "host": { "type": "keyword" },
      "program": { "type": "keyword" },
      "message": { "type": "text" }
    } }
  }
}
```

- [ ] **Step 3: ISM policy** — `deploy/opensearch/ism-policy.json` (a hot→delete policy; the `{{RETENTION_DAYS}}` token is substituted by the CLI):
```json
{
  "policy": {
    "description": "OPNGMS log retention",
    "default_state": "hot",
    "states": [
      { "name": "hot", "actions": [], "transitions": [ { "state_name": "delete", "conditions": { "min_index_age": "{{RETENTION_DAYS}}d" } } ] },
      { "name": "delete", "actions": [ { "delete": {} } ], "transitions": [] }
    ],
    "ism_template": [ { "index_patterns": ["opngms-logs-*"], "priority": 100 } ]
  }
}
```

- [ ] **Step 4: Bootstrap CLI** — add to `app/cli.py` a `syslog-bootstrap` command that:
  (a) `ensure_ca()` (creates the CA if absent) and writes `CA.pem`, the receiver `server.pem`/`server.key` (via `issue_server_cert(hostname=syslog_receiver_host)`) into a target dir (arg `--cert-dir`, default `/certs`);
  (b) PUTs the index template (`PUT {opensearch_url}/_index_template/opngms-logs`) and the ISM policy (`PUT {opensearch_url}/_plugins/_ism/policies/opngms-logs-retention` with `{{RETENTION_DAYS}}` → `log_retention_days`), using `httpx` with basic auth.
  Show the full command code (argparse subcommand mirroring the existing `mfa-reset` CLI structure). Idempotent (PUTs overwrite; cert files written only if absent unless `--force`).

  ```python
  # sketch — implement fully against the existing app/cli.py argparse structure
  async def syslog_bootstrap(cert_dir: str, force: bool) -> None:
      from pathlib import Path
      import httpx, json
      from app.core.config import get_settings
      from app.core.db import make_engine
      from sqlalchemy.ext.asyncio import async_sessionmaker
      from app.services.log_forwarding import SyslogCaService
      from app.services.syslog_ca import issue_server_cert
      s = get_settings()
      engine = make_engine(s.admin_database_url or s.database_url)
      async with async_sessionmaker(engine)() as session:
          ca = await SyslogCaService(session).ensure_ca(); await session.commit()
      server_pem, server_key = issue_server_cert(ca.cert_pem.encode(),
          __import__("app.core.crypto", fromlist=["decrypt_bytes"]).decrypt_bytes(bytes(ca.key_enc)),
          hostname=s.syslog_receiver_host)
      d = Path(cert_dir); d.mkdir(parents=True, exist_ok=True)
      for name, data in [("CA.pem", ca.cert_pem.encode()), ("server.pem", server_pem), ("server.key", server_key)]:
          p = d / name
          if force or not p.exists(): p.write_bytes(data)
      tpl = json.loads(Path("deploy/opensearch/index-template.json").read_text())
      ism = json.loads(Path("deploy/opensearch/ism-policy.json").read_text().replace("{{RETENTION_DAYS}}", str(s.log_retention_days)))
      auth = (s.opensearch_user, s.opensearch_password)
      async with httpx.AsyncClient(verify=False) as c:
          await c.put(f"{s.opensearch_url}/_index_template/opngms-logs", json=tpl, auth=auth)
          await c.put(f"{s.opensearch_url}/_plugins/_ism/policies/opngms-logs-retention", json=ism, auth=auth)
  ```

- [ ] **Step 5: Test** — a unit test for the ISM token substitution + that the template JSON is valid and targets `opngms-logs-*`:
```python
# backend/tests/test_syslog_bootstrap.py
import json
from pathlib import Path


def test_index_template_targets_logs():
    tpl = json.loads(Path("deploy/opensearch/index-template.json").read_text())
    assert tpl["index_patterns"] == ["opngms-logs-*"]
    props = tpl["template"]["mappings"]["properties"]
    assert props["tenant_id"]["type"] == "keyword" and props["device_id"]["type"] == "keyword"


def test_ism_retention_token_substitutes():
    raw = Path("deploy/opensearch/ism-policy.json").read_text().replace("{{RETENTION_DAYS}}", "30")
    pol = json.loads(raw)
    cond = pol["policy"]["states"][0]["transitions"][0]["conditions"]["min_index_age"]
    assert cond == "30d"
```
Run from `backend/` (paths are relative to repo root — run pytest with `cwd` at repo root or adjust the `Path(...)` to `Path(__file__).parents[2] / "deploy/..."`). Use the `parents[2]` form to be cwd-independent.

- [ ] **Step 6: Commit**

```bash
git add backend/deploy/opensearch/ backend/app/cli.py backend/app/core/config.py backend/tests/test_syslog_bootstrap.py
git commit -m "feat(syslog): OpenSearch index template + ISM retention + bootstrap CLI"
```
(Note: place `deploy/opensearch/` at the repo root or under `backend/` consistently — the README + compose must reference the same path. The compose in C2 mounts these.)

---

## Task C2: syslog-ng config + docker-compose.logs.yml

**Files:**
- Create: `deploy/syslog-ng/syslog-ng.conf`, `docker-compose.logs.yml`
- Modify: `.env.example`, `README.md`

- [ ] **Step 1: syslog-ng config** — `deploy/syslog-ng/syslog-ng.conf`:
```conf
@version: 4.5
@include "scl.conf"

source s_tls {
    network(
        transport("tls")
        port(6514)
        flags(syslog-protocol)
        tls(
            ca-file("/certs/CA.pem")
            cert-file("/certs/server.pem")
            key-file("/certs/server.key")
            peer-verify(required-trusted)
        )
    );
};

# Derive tenant/device from the VERIFIED peer cert subject (CN=<device_id>, O=<tenant_id>).
parser p_attr {
    # ${.tls.x509_cn} is the CN (device_id); parse O= out of the full subject DN.
    csv-parser(prefix(".attr.") template("${.tls.x509_subject}")
               delimiters("/") flags(greedy));
};

# Suricata EVE lines are JSON; firewall filterlog lines are CSV after "filterlog:".
parser p_eve { json-parser(prefix(".eve.") template("${MESSAGE}")); };

destination d_opensearch {
    opensearch(
        url("`echo $OPENSEARCH_URL`")
        index("opngms-logs-${YEAR}.${MONTH}.${DAY}")
        type("_doc")
        user("`echo $OPENSEARCH_USER`")
        password("`echo $OPENSEARCH_PASSWORD`")
        template("$(format-json --scope rfc5424 tenant_id=${.attr.O} device_id=${.tls.x509_cn} host=${HOST} program=${PROGRAM} message=${MESSAGE} .eve.*)")
    );
};

log { source(s_tls); parser(p_attr); parser(p_eve); destination(d_opensearch); };
```
> The `csv-parser` on the subject DN splits on `/` and extracts RDNs; refine the field extraction so
> `${.attr.O}` holds the O= value (syslog-ng exposes `${.tls.x509_subject}` as e.g. `/O=tenant/CN=device`
> or `CN=device,O=tenant` depending on build — verify the exact format against the receiver image and
> adjust the parser; the goal is `tenant_id=O`, `device_id=CN`). If the running syslog-ng build lacks
> the native `opensearch()` driver, substitute the `elasticsearch-http()`/`http()` driver POSTing to
> `${OPENSEARCH_URL}/opngms-logs-.../_doc` with the same JSON body. This is finalized against the
> chosen image in this task (pick an image that ships the driver, e.g. a recent `balabit/syslog-ng`).

- [ ] **Step 2: Compose override** — `docker-compose.logs.yml`:
```yaml
# Opt-in log lake: bring up with
#   docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml up -d
services:
  opensearch:
    image: opensearchproject/opensearch:2.17.1
    environment:
      discovery.type: single-node
      bootstrap.memory_lock: "true"
      OPENSEARCH_JAVA_OPTS: "-Xms512m -Xmx512m"
      OPENSEARCH_INITIAL_ADMIN_PASSWORD: ${OPENSEARCH_PASSWORD}
      TZ: ${TZ:-UTC}
    ulimits: { memlock: { soft: -1, hard: -1 } }
    volumes: [ opngms_os:/usr/share/opensearch/data ]
    restart: unless-stopped

  syslog-bootstrap:
    image: opngms-backend:latest
    command: ["python", "-m", "app.cli", "syslog-bootstrap", "--cert-dir", "/certs"]
    env_file: .env
    environment: { TZ: ${TZ:-UTC} }
    volumes: [ opngms_syslog_certs:/certs ]
    depends_on:
      opensearch: { condition: service_started }
      migrate: { condition: service_completed_successfully }
    restart: "no"

  syslog-ng:
    image: balabit/syslog-ng:4.5.0
    command: ["--no-caps", "-F"]
    environment:
      OPENSEARCH_URL: ${OPENSEARCH_URL:-https://opensearch:9200}
      OPENSEARCH_USER: ${OPENSEARCH_USER:-admin}
      OPENSEARCH_PASSWORD: ${OPENSEARCH_PASSWORD}
      TZ: ${TZ:-UTC}
    ports: [ "${SYSLOG_TLS_PORT:-6514}:6514" ]
    volumes:
      - ./deploy/syslog-ng/syslog-ng.conf:/etc/syslog-ng/syslog-ng.conf:ro
      - opngms_syslog_certs:/certs:ro
    depends_on:
      syslog-bootstrap: { condition: service_completed_successfully }
    restart: unless-stopped

volumes:
  opngms_os:
  opngms_syslog_certs:
```

- [ ] **Step 3: `.env.example`** — add a "Log lake (optional)" block: `SYSLOG_RECEIVER_HOST`, `SYSLOG_TLS_PORT=6514`, `OPENSEARCH_URL`, `OPENSEARCH_USER=admin`, `OPENSEARCH_PASSWORD=change-me-strong-opensearch-password`, `LOG_RETENTION_DAYS=30`. (`OPENSEARCH_PASSWORD` joins the `assert_secure_secrets` placeholders? No — keep it out of the boot guard since the log lake is optional; document it must be set when the override is used.)

- [ ] **Step 4: Validate compose** — create a temp `.env` (from `.env.example`), run `docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml config -q` (expect valid), then remove the temp `.env`.

- [ ] **Step 5: README** — add a "Log lake (optional, Phase 1)" section: what it is, the opt-in `up` command, that devices are provisioned per-device via the API (`…/log-forwarding/enable`), and the mTLS port must be reachable by devices. Note search UI is Phase 2.

- [ ] **Step 6: Commit**

```bash
git add deploy/syslog-ng/syslog-ng.conf docker-compose.logs.yml .env.example README.md
git commit -m "feat(syslog): opt-in OpenSearch + syslog-ng compose (mTLS receiver)"
```

---

## Task C3: Scripted pipeline integration check

**Files:**
- Create: `scripts/syslog_pipeline_smoke.py`

- [ ] **Step 1: Write the script** — brings up nothing itself; assumes the log-lake compose is running. It: (a) loads the CA from the DB, issues a throwaway client cert `CN=dev-smoke, O=tenant-smoke`; (b) opens a TLS socket to `localhost:${SYSLOG_TLS_PORT}` presenting that cert; (c) sends one RFC5424 syslog line; (d) polls `${OPENSEARCH_URL}/opngms-logs-*/_search` for a doc with `device_id=dev-smoke, tenant_id=tenant-smoke`; prints PASS/FAIL. Pure stdlib `ssl`+`socket` + `httpx`. This is a manual/CI-infra check, not part of the unit suite.

- [ ] **Step 2: Document** how to run it (in the script docstring + the README log-lake section): `docker compose -f … -f docker-compose.logs.yml up -d`, then `python scripts/syslog_pipeline_smoke.py`.

- [ ] **Step 3: Commit**

```bash
git add scripts/syslog_pipeline_smoke.py
git commit -m "test(syslog): scripted mTLS->OpenSearch pipeline smoke check"
```

---

## Final verification

- [ ] **Backend suite:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Compose:** `docker compose -f docker-compose.prod.yml -f docker-compose.logs.yml config -q` valid.
- [ ] **Live box verify** (Task B2 Step 10) recorded.
- [ ] **Pipeline smoke** (C3) run once against the local log-lake compose (PASS).
- [ ] **Security review:** dispatch `security-reviewer` (CA key encryption + never exposed; mTLS `required-trusted`; tenant attribution from the verified cert not the payload; provisioning RBAC/CSRF/tenant-scoping; OpenSearch not exposed; the cert+key transit to the box). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge per protected-main.

---

## Self-review notes (author)

- **Spec coverage:** CA + key-enc + issue server/device (A2) ✓; models + migration + RLS (A1) ✓; connector trust/syslog methods (B1) ✓; provisioning enable/disable/status API (B2) ✓; OpenSearch index template + ISM + bootstrap (C1) ✓; opt-in compose + syslog-ng mTLS receiver + cert-subject attribution + filterlog/EVE parse (C2) ✓; scripted pipeline check (C3) ✓; cert subject `CN=device_id,O=tenant_id` (A2 + parser C2) ✓; security (mTLS required-trusted, CA enc, no UI) ✓. Phase 2/3 correctly out of scope.
- **Type consistency:** `issue_device_cert(ca_cert_pem, ca_key_pem, *, tenant_id, device_id)` and `issue_server_cert(..., *, hostname)` identical A2/B2/C1; `provision_device(session, *, tenant_id, device_id, client, receiver_host, receiver_port)` B2; connector method names match B1↔B2 (`import_ca`/`import_cert`/`add_syslog_destination`/`delete_*`).
- **Risk flags for the implementer:**
  - The syslog-ng `${.tls.x509_subject}` format + native `opensearch()` driver availability MUST be verified against the chosen image (C2 Step 1 notes the `http()` fallback).
  - **OpenSearch TLS:** the `verify=False` shown in the C1 bootstrap sketch is a PLACEHOLDER — do NOT ship it. The OpenSearch 2.17 image enables the security plugin + a self-signed transport cert by default. The correct approach: configure OpenSearch with a cert signed by our CA (or a dedicated internal CA) and have the bootstrap + the Phase-2 backend client **verify against that CA** (mount `CA.pem` and pass it as the httpx `verify=` path). Alternatively, for the internal-only single-node deployment, disable the OpenSearch HTTPS layer (plain HTTP on the internal network, never published) so there is no cert to verify — both are acceptable; pick one in C1/C2 and document it. Never disable verification against a real/remote OpenSearch.
  - These are config-finalization points within C1/C2, not design gaps.

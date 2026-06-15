"""Unit tests for the CRL hard-revoke building blocks (PR2).

`build_crl` is pure (no DB); `refresh_syslog_crl` reads the CA + ledger owner-side and writes a
hash-named CRL onto the cert volume.
"""
import uuid
from datetime import UTC, datetime, timedelta

from cryptography import x509
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.syslog_ca import build_ca, build_crl
from app.services.syslog_crl import refresh_syslog_crl
from tests.factories import seed_syslog_ca


def _ca_pub(ca_cert_pem: bytes):
    return x509.load_pem_x509_certificate(ca_cert_pem).public_key()


# --------------------------------------------------------------------------- build_crl (pure)

def test_build_crl_lists_revoked_serial():
    ca_cert_pem, ca_key_pem = build_ca()
    now = datetime.now(UTC)
    crl_pem = build_crl(ca_cert_pem, ca_key_pem, [(0x1234, now)])
    crl = x509.load_pem_x509_crl(crl_pem)
    entry = crl.get_revoked_certificate_by_serial_number(0x1234)
    assert entry is not None
    assert entry.serial_number == 0x1234


def test_build_crl_validates_against_ca():
    ca_cert_pem, ca_key_pem = build_ca()
    crl_pem = build_crl(ca_cert_pem, ca_key_pem, [(0xABCD, datetime.now(UTC))])
    crl = x509.load_pem_x509_crl(crl_pem)
    # Signed by the CA whose subject is the CRL issuer.
    assert crl.is_signature_valid(_ca_pub(ca_cert_pem))
    ca_cert = x509.load_pem_x509_certificate(ca_cert_pem)
    assert crl.issuer == ca_cert.subject


def test_build_crl_next_update_is_in_the_future():
    ca_cert_pem, ca_key_pem = build_ca()
    crl_pem = build_crl(ca_cert_pem, ca_key_pem, [], next_update_days=30)
    crl = x509.load_pem_x509_crl(crl_pem)
    assert crl.next_update_utc > datetime.now(UTC)
    # last_update is slightly in the past (clock-skew tolerance).
    assert crl.last_update_utc <= datetime.now(UTC)


def test_build_crl_empty_is_valid():
    ca_cert_pem, ca_key_pem = build_ca()
    crl_pem = build_crl(ca_cert_pem, ca_key_pem, [])
    crl = x509.load_pem_x509_crl(crl_pem)
    assert len(crl) == 0
    assert crl.is_signature_valid(_ca_pub(ca_cert_pem))


def test_build_crl_multiple_serials():
    ca_cert_pem, ca_key_pem = build_ca()
    now = datetime.now(UTC)
    revoked = [(0x01, now - timedelta(days=1)), (0x02, now), (0x03, now - timedelta(hours=1))]
    crl_pem = build_crl(ca_cert_pem, ca_key_pem, revoked)
    crl = x509.load_pem_x509_crl(crl_pem)
    assert {0x01, 0x02, 0x03} <= {e.serial_number for e in crl}


# --------------------------------------------------------------------------- refresh_syslog_crl (DB)

async def _insert_revoked(db_engine, tenant_id: uuid.UUID, device_id: uuid.UUID, serial_hex: str):
    """Insert a tenants/devices/revoked_syslog_certs chain owner-side (RLS-exempt)."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
            {"i": tenant_id, "n": f"T-{serial_hex}", "sl": f"t-{serial_hex}"},
        )
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,"
                 "verify_tls,status,tags) VALUES "
                 "(:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": device_id, "t": tenant_id},
        )
        await s.execute(
            text("INSERT INTO revoked_syslog_certs (id,tenant_id,device_id,serial,reason) "
                 "VALUES (:i,:t,:d,:s,'test')"),
            {"i": uuid.uuid4(), "t": tenant_id, "d": device_id, "s": serial_hex},
        )
        await s.commit()


async def test_refresh_writes_hash_named_crl_revoking_all_tenants(db_engine, tmp_path):
    await seed_syslog_ca(db_engine)
    # Two revoked certs across two tenants — owner read must span both.
    await _insert_revoked(db_engine, uuid.uuid4(), uuid.uuid4(), "1a2b")
    await _insert_revoked(db_engine, uuid.uuid4(), uuid.uuid4(), "00ff")

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        n = await refresh_syslog_crl(session, str(tmp_path))
    assert n == 2

    crl_dir = tmp_path / "crl"
    files = list(crl_dir.glob("*.r0"))
    assert len(files) == 1, files
    crl = x509.load_pem_x509_crl(files[0].read_bytes())
    serials = {e.serial_number for e in crl}
    assert serials == {0x1a2b, 0x00ff}

    # The CRL is signed by the seeded CA.
    async with factory() as session:
        ca = (await session.execute(text("SELECT cert_pem FROM syslog_ca LIMIT 1"))).scalar_one()
    ca_cert = x509.load_pem_x509_certificate(ca.encode())
    assert crl.is_signature_valid(ca_cert.public_key())

    # The filename is the OpenSSL issuer hash of the CRL + ".r0" (computed via the same CLI
    # syslog-ng relies on for crl-dir lookup).
    import subprocess
    out = subprocess.run(
        ["openssl", "crl", "-hash", "-noout", "-in", str(files[0])],
        capture_output=True, text=True, check=True,
    )
    assert files[0].name == f"{out.stdout.strip()}.r0"


async def test_refresh_empty_ledger_writes_empty_crl(db_engine, tmp_path):
    await seed_syslog_ca(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        n = await refresh_syslog_crl(session, str(tmp_path))
    assert n == 0
    files = list((tmp_path / "crl").glob("*.r0"))
    assert len(files) == 1
    crl = x509.load_pem_x509_crl(files[0].read_bytes())
    assert len(crl) == 0


async def test_refresh_skips_when_cert_dir_none(db_engine):
    await seed_syslog_ca(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await refresh_syslog_crl(session, None) == "skipped"


async def test_refresh_skips_when_cert_dir_missing(db_engine, tmp_path):
    await seed_syslog_ca(db_engine)
    missing = tmp_path / "does-not-exist"
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert await refresh_syslog_crl(session, str(missing)) == "skipped"
    assert not missing.exists()


async def test_refresh_skips_when_ca_absent(db_engine, tmp_path):
    # No seed_syslog_ca → no CA row.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM syslog_ca"))).scalar_one() == 0
        assert await refresh_syslog_crl(session, str(tmp_path)) == "skipped"
    assert not (tmp_path / "crl").exists()

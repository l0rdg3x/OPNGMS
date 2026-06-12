"""Pure x509 CA primitives for the log pipeline (no DB). Built on `cryptography`."""
from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
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

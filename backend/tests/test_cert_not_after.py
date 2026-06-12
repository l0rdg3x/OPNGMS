from datetime import UTC, datetime, timedelta

from cryptography import x509

from app.services.syslog_ca import build_ca, cert_not_after, issue_device_cert, issue_server_cert


def test_cert_not_after_is_aware_and_future():
    ca_cert, ca_key = build_ca()
    cert_pem, _ = issue_device_cert(ca_cert, ca_key, tenant_id="t1", device_id="d1")
    exp = cert_not_after(cert_pem)
    assert isinstance(exp, datetime)
    assert exp.tzinfo is not None
    assert exp > datetime.now(UTC)


def test_device_cert_lifetime_is_short_and_overridable():
    # Short default (90d) bounds a stolen-key window; the `days` param is honoured.
    ca_cert, ca_key = build_ca()
    default_exp = cert_not_after(issue_device_cert(ca_cert, ca_key, tenant_id="t", device_id="d")[0])
    assert default_exp < datetime.now(UTC) + timedelta(days=120)  # not the old 730d
    short_exp = cert_not_after(issue_device_cert(ca_cert, ca_key, tenant_id="t", device_id="d", days=7)[0])
    assert short_exp < datetime.now(UTC) + timedelta(days=8)


def test_chain_has_ski_aki_for_strict_openssl():
    # Strict OpenSSL 3.x (clients, syslog-ng, OPNsense) refuses chains whose leaf lacks an
    # AuthorityKeyIdentifier matching the CA's SubjectKeyIdentifier.
    ca_pem, ca_key = build_ca()
    ca = x509.load_pem_x509_certificate(ca_pem)
    ca_ski = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value

    for cert_pem, _ in (
        issue_device_cert(ca_pem, ca_key, tenant_id="t1", device_id="d1"),
        issue_server_cert(ca_pem, ca_key, hostname="logs.example"),
    ):
        leaf = x509.load_pem_x509_certificate(cert_pem)
        leaf.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)  # present
        aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
        assert aki.key_identifier == ca_ski.digest  # AKI points at the CA's key

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
    ca = x509.load_pem_x509_certificate(ca_cert_pem)
    assert cert.issuer == ca.subject
    from cryptography.hazmat.primitives.asymmetric import padding
    ca.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                           padding.PKCS1v15(), cert.signature_hash_algorithm)


def test_issue_server_cert_has_san_and_server_eku():
    ca_cert_pem, ca_key_pem = build_ca()
    cert_pem, _ = issue_server_cert(ca_cert_pem, ca_key_pem, hostname="logs.opngms.example")
    cert = x509.load_pem_x509_certificate(cert_pem)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "logs.opngms.example" in san.get_values_for_type(x509.DNSName)
    eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in eku

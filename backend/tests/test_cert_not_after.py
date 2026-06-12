from datetime import UTC, datetime

from app.services.syslog_ca import build_ca, cert_not_after, issue_device_cert


def test_cert_not_after_is_aware_and_future():
    ca_cert, ca_key = build_ca()
    cert_pem, _ = issue_device_cert(ca_cert, ca_key, tenant_id="t1", device_id="d1")
    exp = cert_not_after(cert_pem)
    assert isinstance(exp, datetime)
    assert exp.tzinfo is not None
    assert exp > datetime.now(UTC)

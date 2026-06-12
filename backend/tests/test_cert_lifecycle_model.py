from app.core.rls import TENANT_TABLES
from app.models.revoked_syslog_cert import RevokedSyslogCert


def test_ledger_table_registered_for_rls():
    assert "revoked_syslog_certs" in TENANT_TABLES


def test_ledger_model_columns():
    cols = RevokedSyslogCert.__table__.columns.keys()
    assert {"id", "tenant_id", "device_id", "serial", "reason", "revoked_at"} <= set(cols)
    assert RevokedSyslogCert.__tablename__ == "revoked_syslog_certs"

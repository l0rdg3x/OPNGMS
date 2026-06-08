from app.models import Base
from app.models.device import Device


def test_all_tables_registered():
    names = set(Base.metadata.tables.keys())
    assert {
        "tenants",
        "users",
        "memberships",
        "devices",
        "audit_log",
        "sessions",
    } <= names


def test_device_has_tenant_and_encrypted_secret_columns():
    cols = {c.name for c in Device.__table__.columns}
    assert "tenant_id" in cols
    assert "api_key_enc" in cols
    assert "api_secret_enc" in cols
    assert "status" in cols

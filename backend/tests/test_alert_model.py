from app.models import Base
from app.models.alert import Alert


def test_alert_table_registered():
    assert "alerts" in Base.metadata.tables
    cols = {c.name for c in Alert.__table__.columns}
    assert {"id", "tenant_id", "device_id", "type", "label", "severity", "opened_at", "resolved_at", "details"} <= cols

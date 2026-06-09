from app.models import Base
from app.models.metric import Metric


def test_metric_table_registered():
    assert "metrics" in Base.metadata.tables
    cols = {c.name for c in Metric.__table__.columns}
    assert {"time", "device_id", "tenant_id", "metric", "label", "value"} <= cols

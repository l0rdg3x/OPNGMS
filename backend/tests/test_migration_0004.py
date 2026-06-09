from app.models.tenant import Tenant


def test_timestamp_mixin_has_updated_at():
    cols = {c.name for c in Tenant.__table__.columns}
    assert "updated_at" in cols

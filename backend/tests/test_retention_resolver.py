from app.services.retention import RETENTION_STORES, effective_retention_days


def test_resolver_precedence():
    assert RETENTION_STORES == ("perimeter", "events", "metrics", "log_lake")
    assert effective_retention_days("perimeter", global_default=30, tenant_override=None) == 30
    assert effective_retention_days("perimeter", global_default=30, tenant_override={"perimeter": 7}) == 7
    # invalid / out-of-range / wrong-type overrides fall back to the global default
    for bad in ({"perimeter": 0}, {"perimeter": -1}, {"perimeter": 99999}, {"perimeter": "x"}, {"perimeter": True}):
        assert effective_retention_days("perimeter", global_default=30, tenant_override=bad) == 30

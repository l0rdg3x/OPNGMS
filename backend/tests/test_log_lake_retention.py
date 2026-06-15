"""SP-2 log-lake retention: pure index parsing + the per-tenant delete decision.

These tests cover the no-infra logic (Task 3). The OpenSearch purge (Task 4) is mocked with respx below.
"""
from datetime import date

from app.services.log_lake_retention import indices_to_delete, parse_index

_UUID = "3f6a7b8c-1d2e-4f50-9a1b-2c3d4e5f6071"


def test_parse_index_tenant_tagged():
    assert parse_index(f"opngms-logs-{_UUID}-2026.06.10") == (_UUID, date(2026, 6, 10))


def test_parse_index_legacy_date_only():
    # No tenant segment → tenant_id None (legacy shared index).
    assert parse_index("opngms-logs-2026.06.10") == (None, date(2026, 6, 10))


def test_parse_index_non_matching():
    assert parse_index("opngms-logs-weird") is None
    assert parse_index("other-index") is None
    # A 36-char-shaped segment that is NOT a valid UUID is rejected.
    assert parse_index("opngms-logs-zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz-2026.06.10") is None
    # An out-of-range date is rejected (no silent ValueError leak).
    assert parse_index("opngms-logs-2026.13.40") is None


def test_indices_to_delete_per_tenant_and_legacy():
    today = date(2026, 6, 20)
    aaaa = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
    bbbb = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
    names = [
        f"opngms-logs-{aaaa}-2026.06.01",  # tenant aaaa, 19d old
        f"opngms-logs-{aaaa}-2026.06.19",  # tenant aaaa, 1d old (kept)
        f"opngms-logs-{bbbb}-2026.06.01",  # tenant bbbb, 19d old
        "opngms-logs-2026.05.01",          # legacy, 50d old
        "unrelated-index",                 # ignored (not ours)
    ]
    overrides = {aaaa: {"log_lake": 7}}  # aaaa keeps 7d; bbbb + legacy use the global 30
    to_del = indices_to_delete(names, today, global_default=30, overrides_by_tenant=overrides)
    # aaaa@7d: 19d>7 -> delete the 06.01; 1d kept. bbbb@30d: 19d<30 kept. legacy@30d: 50d>30 -> delete.
    assert set(to_del) == {f"opngms-logs-{aaaa}-2026.06.01", "opngms-logs-2026.05.01"}


def test_indices_to_delete_boundary_is_strict_greater_than():
    # Exactly `days` old is kept; older than `days` is deleted (strict >, matching the SP-1 cutoff).
    today = date(2026, 6, 20)
    names = ["opngms-logs-2026.05.21", "opngms-logs-2026.05.20"]  # 30d and 31d old
    to_del = indices_to_delete(names, today, global_default=30, overrides_by_tenant={})
    assert to_del == ["opngms-logs-2026.05.20"]

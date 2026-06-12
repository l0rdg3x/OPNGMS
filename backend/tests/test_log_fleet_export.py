from datetime import UTC, datetime, timedelta

from app.services.log_fleet_export import fleet_rows_to_csv, fleet_rows_to_html

_NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
_STALE = timedelta(hours=1)


def _rows():
    return [
        {"tenant_id": "a", "tenant_name": "Acme", "enabled": 2, "disabled": 0, "revoked": 1,
         "total_devices": 3, "last_log_at": None, "volume": None},                       # silent (no logs)
        {"tenant_id": "b", "tenant_name": "Beta & Co", "enabled": 1, "disabled": 1, "revoked": 0,
         "total_devices": 2, "last_log_at": _NOW - timedelta(minutes=5), "volume": 42},   # fresh -> not silent
        {"tenant_id": "c", "tenant_name": "Gamma", "enabled": 0, "disabled": 1, "revoked": 0,
         "total_devices": 1, "last_log_at": None, "volume": None},                        # no forwarding -> not silent
    ]


def test_csv_has_header_and_rows_with_silent_column():
    csv_text = fleet_rows_to_csv(_rows(), now=_NOW, stale_after=_STALE)
    lines = csv_text.strip().splitlines()
    assert lines[0] == "tenant_name,enabled,disabled,revoked,total_devices,last_log_at,volume,silent"
    assert lines[1] == "Acme,2,0,1,3,,,yes"                       # enabled + no logs -> silent yes
    assert lines[2].startswith("Beta & Co,1,1,0,2,2026-06-12T11:55:00")
    assert lines[2].endswith(",42,no")                            # fresh log -> not silent
    assert lines[3] == "Gamma,0,1,0,1,,,no"                       # no enabled forwarding -> not silent


def test_html_escapes_and_lists_rows():
    html = fleet_rows_to_html(_rows(), window="7d", generated_at=_NOW, now=_NOW, stale_after=_STALE)
    assert "<table" in html and "</table>" in html
    assert "Beta &amp; Co" in html          # ampersand escaped (no raw &)
    assert "Beta & Co" not in html
    assert "7d" in html                      # window labelled
    assert "Acme" in html and "Gamma" in html


def test_csv_neutralises_formula_injection_in_tenant_name():
    rows = [{"tenant_name": "=cmd|'/c calc'!A1", "enabled": 1, "disabled": 0, "revoked": 0,
             "total_devices": 1, "last_log_at": None, "volume": None}]
    csv_text = fleet_rows_to_csv(rows, now=_NOW, stale_after=_STALE)
    data = csv_text.strip().splitlines()[1]
    assert data.startswith("'=cmd")  # leading apostrophe -> spreadsheet treats it as text, not a formula


def test_csv_empty_rows_is_header_only():
    csv_text = fleet_rows_to_csv([], now=_NOW, stale_after=_STALE)
    assert csv_text.strip().splitlines() == [
        "tenant_name,enabled,disabled,revoked,total_devices,last_log_at,volume,silent"]

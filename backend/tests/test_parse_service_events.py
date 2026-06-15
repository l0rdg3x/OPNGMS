from app.connectors.opnsense.parsers import parse_service_events


def _row(process, line, severity="notice", ts="2026-06-15T10:00:00"):
    return {"timestamp": ts, "process_name": process, "severity": severity, "pid": "1", "line": line}


def _data(rows):
    return {"rows": rows}


def test_classifies_reboot():
    out = parse_service_events(_data([_row("shutdown", "reboot by root", "notice")]))
    assert len(out) == 1 and out[0]["category"] == "reboot" and out[0]["name"] == "reboot"


def test_classifies_service_crash():
    out = parse_service_events(_data([_row(
        "kernel", "pid 42 (suricata), jid 0, uid 0: exited on signal 11 (core dumped)", "crit")]))
    assert out[0]["category"] == "service" and out[0]["name"] == "service_crashed"
    assert out[0]["severity"] == "high"


def test_classifies_disk_full():
    out = parse_service_events(_data([_row("kernel", "/var: filesystem full", "err")]))
    assert out[0]["category"] == "disk" and out[0]["name"] == "filesystem_full"
    assert out[0]["severity"] == "high"


def test_drops_noise():
    out = parse_service_events(_data([_row("dhcp6c", "advertise contains NoAddrsAvail status", "info")]))
    assert out == []


def test_event_key_is_stable_and_carries_attributes():
    rows = [_row("shutdown", "reboot by root", "notice")]
    a = parse_service_events(_data(rows))
    b = parse_service_events(_data(rows))
    assert a[0]["event_key"] == b[0]["event_key"]
    assert a[0]["attributes"]["process"] == "shutdown"
    assert a[0]["attributes"]["message"] == "reboot by root"
    assert a[0]["attributes"]["log_severity"] == "notice"


def test_first_matching_rule_wins_and_only_one_event_per_row():
    # A single row never yields more than one classified event.
    out = parse_service_events(_data([_row("kernel", "/usr: no space left on device", "err")]))
    assert len(out) == 1 and out[0]["category"] == "disk"


def test_fail_safe_on_malformed_rows():
    # Non-dict rows / a non-dict envelope must never raise; they are skipped.
    assert parse_service_events({"rows": [None, 5, "x"]}) == []
    assert parse_service_events(None) == []
    assert parse_service_events([]) == []


def test_severity_escalates_with_high_log_severity():
    # A medium-base rule escalates to high when the log severity is in the high set.
    out = parse_service_events(_data([_row("configd.py", "restarting suricata", "err")]))
    assert out[0]["name"] == "service_restarted" and out[0]["severity"] == "high"


def test_medium_base_severity_when_log_severity_low():
    out = parse_service_events(_data([_row("configd.py", "restarting suricata", "notice")]))
    assert out[0]["name"] == "service_restarted" and out[0]["severity"] == "medium"

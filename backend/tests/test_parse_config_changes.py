from app.connectors.opnsense.parsers import parse_config_changes


def _row(line, process="audit", severity="Notice", ts="2026-06-15T19:26:27"):
    return {"timestamp": ts, "process_name": process, "severity": severity, "pid": "1", "line": line}


def _data(rows):
    return {"rows": rows}


# Real api-channel line (remote, carries the source IP). uuid is stripped from change_ref.
_API = (" user root@192.168.6.100 changed configuration to /conf/backup/config-1781551587.0626.xml in "
        "/api/monit/settings/delTest/2f2d1f72-c3bb-4cf6-a716-c88cf2412754 "
        "/api/monit/settings/delTest/2f2d1f72-c3bb-4cf6-a716-c88cf2412754 made changes")
# Real system-channel line (local/script form `(root)`, no IP).
_SYS = (" user (root) changed configuration to /conf/backup/config-1781551620.8666.xml in "
        "/usr/local/opnsense/scripts/firmware/register.php "
        "/usr/local/opnsense/scripts/firmware/register.php made changes")
# Synthesized gui-channel line (legacy WebGUI page, remote).
_GUI = (" user admin@10.0.0.5 changed configuration to /conf/backup/config-1781551999.1.xml in "
        "/firewall_rules.php /firewall_rules.php made changes")


def test_api_change_is_info_not_drift():
    out = parse_config_changes(_data([_row(_API)]))
    assert len(out) == 1
    e = out[0]
    assert e["action"] == "api"          # channel
    assert e["severity"] == "info"
    assert e["category"] == "monit"      # area
    assert e["name"] == "root"           # actor
    assert e["src_ip"] == "192.168.6.100"
    assert e["attributes"]["channel"] == "api"
    assert e["attributes"]["change_ref"] == "/api/monit/settings/delTest"   # trailing uuid stripped
    assert e["attributes"]["backup_file"] == "config-1781551587.0626.xml"


def test_system_change_is_drift_medium_local_actor_no_ip():
    out = parse_config_changes(_data([_row(_SYS)]))
    e = out[0]
    assert e["action"] == "system" and e["severity"] == "medium"
    assert e["name"] == "root" and e["src_ip"] == ""        # local form -> no IP
    assert e["attributes"]["channel"] == "system"


def test_gui_change_is_drift_medium():
    out = parse_config_changes(_data([_row(_GUI)]))
    e = out[0]
    assert e["action"] == "gui" and e["severity"] == "medium"
    assert e["category"] == "firewall" and e["name"] == "admin" and e["src_ip"] == "10.0.0.5"


def test_drops_non_audit_and_non_config_lines():
    rows = [
        _row(_API, process="configd.py"),                         # wrong process -> skip
        _row(" action allowed system.diag.log for user root"),    # audit, but not a config change
        _row(" user root@1.2.3.4 authentication failed"),         # failed-login audit line -> skip
        _row("garbage"),
    ]
    assert parse_config_changes(_data(rows)) == []


def test_event_key_stable_and_dedups_on_backup_file():
    a = parse_config_changes(_data([_row(_API)]))
    b = parse_config_changes(_data([_row(_API)]))
    assert a[0]["event_key"] == b[0]["event_key"]
    # A different save (different backup file) at the same ts -> a different key.
    other = _API.replace("config-1781551587.0626.xml", "config-1781551999.9.xml")
    c = parse_config_changes(_data([_row(other)]))
    assert c[0]["event_key"] != a[0]["event_key"]


def test_fail_safe_on_malformed():
    assert parse_config_changes({"rows": [None, 5, "x"]}) == []
    assert parse_config_changes(None) == []
    assert parse_config_changes([]) == []

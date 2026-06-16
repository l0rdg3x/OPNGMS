from app.connectors.opnsense.parsers import parse_auth_failures, parse_firewall_blocks


def test_parse_firewall_blocks_keeps_blocks_only():
    rows = [
        {"action": "block", "src": "203.0.113.9", "dst": "10.0.0.1", "srcport": "5555",
         "dstport": "23", "interface": "igb0", "protoname": "tcp",
         "__timestamp__": "2026-06-14T10:00:00", "__digest__": "abc123"},
        {"action": "pass", "src": "10.0.0.5", "dst": "8.8.8.8",
         "__timestamp__": "2026-06-14T10:00:01", "__digest__": "def456"},  # pass -> dropped
    ]
    out = parse_firewall_blocks(rows)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "203.0.113.9"
    assert e["event_key"] == "abc123"          # __digest__ is the dedup key
    assert e["name"] == "23"                    # targeted port
    assert e["attributes"]["dstport"] == "23"
    assert e["attributes"]["interface"] == "igb0"
    assert e["time"] is not None


def test_parse_firewall_blocks_skips_blocks_without_src():
    out = parse_firewall_blocks([{"action": "block", "__timestamp__": "2026-06-14T10:00:00"}])
    assert out == []


def test_parse_auth_failures_extracts_user_and_ip():
    rows = {"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "audit",
         "line": " authentication failed for user 'admin' from 203.0.113.7"},
        {"timestamp": "2026-06-14T10:00:01", "process_name": "audit",
         "line": " Successful login for user 'root' from 10.0.0.2"},     # success -> dropped
        {"timestamp": "2026-06-14T10:00:02", "process_name": "configd.py",
         "line": " action allowed system.diag.log for user root"},       # not 'audit' -> dropped
    ]}
    out = parse_auth_failures(rows)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "203.0.113.7"
    assert e["name"] == "admin"             # username attempted
    assert e["attributes"]["username"] == "admin"
    assert e["time"] is not None
    assert e["event_key"]


def test_parse_auth_failures_handles_from_colon_variant():
    # OPNsense session/auth lines also use "from: <ip>".
    rows = {"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "audit",
         "line": " Wrong password for user 'bob' from: 198.51.100.4"},
    ]}
    out = parse_auth_failures(rows)
    assert len(out) == 1 and out[0]["src_ip"] == "198.51.100.4" and out[0]["name"] == "bob"


def test_parse_auth_failures_real_opnsense_webgui_format():
    # Live-verified OPNsense 26.1.10 audit lines. Only the WebGUI auth-error line carries a source IP
    # (the attacker signal); the internal auth-stack failure lines have no IP and are skipped; a
    # successful login is never a failure.
    rows = {"rows": [
        {"timestamp": "2026-06-16T10:00:00", "process_name": "audit",
         "line": "/index.php: Web GUI authentication error for 'root' from 192.168.6.100"},
        {"timestamp": "2026-06-16T10:00:01", "process_name": "audit",
         "line": "user root failed authentication for WebGui on OPNsense\\Auth\\Services\\WebGui"},
        {"timestamp": "2026-06-16T10:00:02", "process_name": "audit",
         "line": "user root could not authenticate for WebGui. [using OPNsense\\Auth\\Services\\WebGui]"},
        {"timestamp": "2026-06-16T10:00:03", "process_name": "audit",
         "line": "user root authenticated successfully for WebGui [using ...]"},  # success
    ]}
    out = parse_auth_failures(rows)
    assert len(out) == 1                                  # only the IP-bearing WebGUI error
    assert out[0]["src_ip"] == "192.168.6.100"
    assert out[0]["name"] == "root"


def test_parse_auth_failures_skips_unrecognized_lines():
    rows = {"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "audit", "line": " random noise line"},
    ]}
    assert parse_auth_failures(rows) == []

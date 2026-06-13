import hashlib

from app.connectors.opnsense import parsers
from tests.opn_fixtures import load


def test_fixtures_load():
    assert load("system_resources.json")["memory"]["total"] == "8462950400"
    assert load("traffic_interface.json")["interfaces"]["wan"]["link state"] == "2"
    assert load("firmware_status.json")["product"]["product_version"] == "26.1.9"


def test_num_handles_units_and_tilde():
    assert parsers.num("12.3 ms") == 12.3
    assert parsers.num("0.0 %") == 0.0
    assert parsers.num("~") == 0.0
    assert parsers.num(5) == 5.0
    assert parsers.num(None) == 0.0


def test_parse_uptime():
    assert parsers.parse_uptime("00:11:14") == 674
    assert parsers.parse_uptime("2 days, 03:00:01") == 2 * 86400 + 3 * 3600 + 1
    assert parsers.parse_uptime("") == 0


def test_parse_cores():
    assert parsers.parse_cores(["Intel(R) ... (2 cores, 4 threads)"]) == 2
    assert parsers.parse_cores([]) == 1


def test_parse_system_info_against_real_fixtures():
    info = parsers.parse_system_info(
        load("system_resources.json"),
        load("system_disk.json"),
        load("system_time.json"),
        load("cpu_type.json"),
    )
    assert info["mem_pct"] == 8.9        # 755341425 / 8462950400 * 100
    assert info["disk_pct"] == 1.0       # used_pct of mountpoint "/"
    assert info["uptime_seconds"] == 674  # 00:11:14
    assert info["cpu_pct"] == 6.0        # load1m 0.12 / 2 cores * 100


def test_parse_interfaces_link_state_up():
    out = parsers.parse_interfaces(load("traffic_interface.json"))
    by = {i["name"]: i for i in out}
    assert by["WAN"]["up"] is True        # link state "2"
    assert by["WAN"]["bytes_in"] == 394684.0
    assert by["WAN"]["bytes_out"] == 5116981.0
    assert by["LAN"]["up"] is False       # link state "0" (unknown / no carrier)


def test_parse_gateways_tilde_and_status():
    out = parsers.parse_gateways(load("gateway_status.json"))
    by = {g["name"]: g for g in out}
    assert by["WAN_DHCP"]["up"] is True   # status "none" is up
    assert by["WAN_DHCP"]["rtt_ms"] == 0.0   # "~" -> 0.0
    assert by["WAN_DHCP"]["loss_pct"] == 0.0
    # a down gateway:
    down = parsers.parse_gateways({"items": [
        {"name": "G2", "status": "down", "delay": "12.3 ms", "loss": "100.0 %"}]})
    assert down[0]["up"] is False and down[0]["rtt_ms"] == 12.3 and down[0]["loss_pct"] == 100.0


def test_parse_vpn_reads_rows():
    assert parsers.parse_vpn(load("wireguard_show_empty.json")) == []
    # Real OPNsense 26.1.9 shape: `peer-status` ("online"/"offline") drives `up`; there is
    # NO `connected` field (verified live by creating a throwaway tunnel on the test box).
    assert parsers.parse_vpn(load("wireguard_show.json")) == [{"name": "wg-site-a", "up": True}]
    assert parsers.parse_vpn(load("wireguard_show_offline.json")) == [{"name": "opngms-probe", "up": False}]
    # Legacy/alternate `connected` field is still honored.
    assert parsers.parse_vpn({"rows": [{"name": "x", "connected": True}]}) == [{"name": "x", "up": True}]


def test_parse_interfaces_tolerates_malformed_shape():
    assert parsers.parse_interfaces({"interfaces": None}) == []
    assert parsers.parse_interfaces({"interfaces": []}) == []   # wrong type (list)
    assert parsers.parse_interfaces({}) == []
    assert parsers.parse_interfaces(None) == []


def test_parse_gateways_force_down_is_down():
    out = parsers.parse_gateways({"items": [{"name": "G3", "status": "force_down"}]})
    assert out[0]["up"] is False


def test_parse_firmware_version_from_product_subtree():
    # firmware/status: version is under product.product_version (no top-level field)
    assert parsers.parse_firmware_version(load("firmware_status.json")) == "26.1.9"
    # firmware/info: top-level product_version present
    assert parsers.parse_firmware_version(load("firmware_info.json")) == "26.1.9"
    assert parsers.parse_firmware_version({}) == ""


def test_parse_plugins_reads_plugin_array_not_package():
    out = parsers.parse_plugins(load("firmware_info.json"))
    assert out["product_version"] == "26.1.9"
    assert out["plugins"] == ["os-wireguard"]   # installed "1" from the `plugin` array
    assert "base" not in out["plugins"]          # `package` array is ignored
    assert "os-theme-cicada" not in out["plugins"]  # installed "0"


def test_parse_plugins_tolerates_malformed_plugin_field():
    assert parsers.parse_plugins({"plugin": None})["plugins"] == []
    assert parsers.parse_plugins({"plugin": "x"})["plugins"] == []   # non-list
    assert parsers.parse_plugins({"plugin": {"a": 1}})["plugins"] == []  # dict, not list
    assert parsers.parse_plugins({})["plugins"] == []
    assert parsers.parse_plugins(None)["plugins"] == []


def test_parse_plugins_available_lists_all_with_install_state():
    info = {"product_version": "26.1.9", "plugin": [
        {"name": "os-wireguard", "installed": "1", "version": "2.6", "locked": "0"},
        {"name": "os-theme-cicada", "installed": "0", "version": "1.40"},
    ]}
    out = parsers.parse_plugins(info)
    assert out["plugins"] == ["os-wireguard"]                 # unchanged: installed names only
    avail = {p["name"]: p for p in out["available"]}
    assert set(avail) == {"os-wireguard", "os-theme-cicada"}
    assert avail["os-wireguard"] == {
        "name": "os-wireguard", "installed": True, "version": "2.6", "locked": False}
    assert avail["os-theme-cicada"]["installed"] is False
    assert avail["os-theme-cicada"]["version"] == "1.40"


def test_parse_plugins_available_tolerates_malformed():
    assert parsers.parse_plugins({"plugin": None})["available"] == []
    assert parsers.parse_plugins({})["available"] == []
    assert parsers.parse_plugins(None)["available"] == []


def test_parse_ids_rows_list_and_dict_and_keys():
    # bare list edge (the empty GET used to crash .get()): must not raise
    assert parsers.parse_ids_rows([]) == []
    out = parsers.parse_ids_rows(load("ids_query_alerts.json"))
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "192.168.1.50"
    assert e["dst_ip"] == "8.8.8.8"        # dest_ip
    assert e["name"] == "ET SCAN Nmap"     # alert.signature
    assert e["severity"] == "2"
    assert e["action"] == "allowed"
    assert e["category"] == "alert"
    assert e["event_key"] == "a1"          # stable alert_id
    assert e["time"].tzinfo is not None


def test_parse_ids_rows_hash_fallback_and_variants():
    payload = {"alerts": [{
        "timestamp": "2026-06-09T13:30:00Z", "src_ip": "10.0.0.7", "dst_ip": "8.8.8.8",
        "signature": "ET POLICY DNS", "severity": 3, "action": "blocked"}]}
    e = parsers.parse_ids_rows(payload)[0]
    assert e["name"] == "ET POLICY DNS" and e["dst_ip"] == "8.8.8.8" and e["severity"] == "3"
    expected = hashlib.sha1("|".join([
        e["time"].isoformat(), "10.0.0.7", "8.8.8.8", "ET POLICY DNS", "3"]).encode()).hexdigest()
    assert e["event_key"] == expected


def test_parse_dns_rows():
    assert parsers.parse_dns_rows([]) == []
    out = parsers.parse_dns_rows(load("unbound_search_queries.json"))
    e = out[0]
    assert e["src_ip"] == "192.168.1.50"
    assert e["name"] == "example.com"
    assert e["action"] == "allowed"
    assert e["category"] == "query"
    assert e["dst_ip"] == "" and e["severity"] == ""
    assert e["event_key"] and e["time"].tzinfo is not None


def test_parsers_skip_non_dict_rows():
    assert parsers.parse_ids_rows({"rows": [None, "x", 42]}) == []
    assert parsers.parse_dns_rows({"rows": [None, "x", 42]}) == []
    assert parsers.parse_gateways({"items": [None, "x"]}) == []
    assert parsers.parse_vpn({"rows": [None, "x"]}) == []


def test_parse_ids_rows_empty_envelope_fixture():
    # the real OPNsense empty shape is a dict {rows:[]}, distinct from the bare-[] edge
    assert parsers.parse_ids_rows(load("ids_query_alerts_empty.json")) == []

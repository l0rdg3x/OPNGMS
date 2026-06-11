from app.connectors.opnsense.profiles import CAPABILITIES
from app.connectors.opnsense.resolver import CapabilityResolver

CAPS = ["system_info", "interfaces", "gateways", "vpn_status", "ids_alerts",
        "dns_events", "firmware_status", "plugin_info", "config_backup"]


def test_every_capability_has_an_unconstrained_default_last():
    for name in CAPS:
        last = CAPABILITIES[name][-1]
        assert last.edition == "any" and last.min_version is None and last.max_version is None


def test_current_device_resolves_to_verified_261_endpoints():
    r = CapabilityResolver("community", "26.1.9")
    paths = {name: [req.path for req in r.resolve(name).requests] for name in CAPS}
    assert paths["interfaces"] == ["diagnostics/traffic/interface"]
    assert paths["gateways"] == ["routes/gateway/status"]
    assert paths["vpn_status"] == ["wireguard/service/show"]
    assert paths["ids_alerts"] == ["ids/service/queryAlerts"]
    assert paths["dns_events"][0].startswith("unbound/overview/searchQueries")
    assert paths["plugin_info"] == ["core/firmware/info"]
    assert paths["firmware_status"] == ["core/firmware/status"]
    assert paths["config_backup"] == ["core/backup/download/this"]
    assert paths["system_info"] == [
        "diagnostics/system/systemResources", "diagnostics/system/systemDisk",
        "diagnostics/system/systemTime", "diagnostics/cpu_usage/getCPUType"]


def test_old_series_resolves_dns_to_legacy_endpoint():
    r = CapabilityResolver("community", "18.7.1")
    assert r.resolve("dns_events").requests[0].path == "unbound/diagnostics/queries"


def test_ids_request_is_post_with_body():
    req = CapabilityResolver("community", "26.1.9").resolve("ids_alerts").requests[0]
    assert req.method == "POST" and req.body["searchPhrase"] == ""


def test_config_backup_is_text():
    req = CapabilityResolver("community", "26.1.9").resolve("config_backup").requests[0]
    assert req.kind == "text"

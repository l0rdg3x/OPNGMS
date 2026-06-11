from app.services.firewall_introspect import infer_rule_fields

_MODEL = {"rule": {
    "enabled": "1",
    "action": {"pass": {"value": "Pass", "selected": 1}, "block": {"value": "Block", "selected": 0}},
    "%action": "Pass",                                   # display-mirror -> dropped
    "interface": {"wan": {"value": "WAN", "selected": 0}, "lan": {"value": "LAN", "selected": 0}},
    "gateway": {"": {"value": "none", "selected": 1}},   # device-specific -> excluded
    "source_net": "any",
    "log": "0",
    "categories": [],                                    # list -> skipped
    "description": "",
}}


def test_infer_rule_fields_excludes_device_and_mirror_fields_and_surfaces_interfaces():
    out = infer_rule_fields(_MODEL)
    paths = {f["path"] for f in out["fields"]}
    assert "action" in paths and "source_net" in paths and "log" in paths and "description" in paths
    assert "enabled" in paths
    # excluded / dropped
    for p in ("interface", "%action", "gateway", "categories", "sort_order", "prio_group"):
        assert p not in paths
    # interface options surfaced separately for the apply picker
    assert {i["value"] for i in out["interfaces"]} == {"wan", "lan"}
    # control inference
    assert next(f for f in out["fields"] if f["path"] == "action")["control"] == "select"
    assert next(f for f in out["fields"] if f["path"] == "log")["control"] == "switch"
    assert next(f for f in out["fields"] if f["path"] == "source_net")["control"] == "text"

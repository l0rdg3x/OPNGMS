import uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import (
    NoInverseError,
    alias_from_config_xml,
    build_inverse,
    has_inverse,
    setting_from_config_xml,
)


def _change(operation, target, payload):
    return ConfigChange(tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
                        kind="alias", operation=operation, target=target, payload=payload,
                        baseline_hash="", status="applied")


def _setting_change(target, payload):
    c = _change("set", target, payload)
    c.kind = "opnsense_setting"
    return c


def test_has_inverse():
    assert has_inverse("alias") is True
    assert has_inverse("opnsense_setting") is True
    assert has_inverse("firewall_rule") is False


def test_add_inverts_to_delete_without_snapshot():
    op, target, payload = build_inverse(_change("add", "WebServers", {"name": "WebServers", "type": "host"}), None)
    assert op == "delete"
    assert target == "WebServers"
    assert payload == {"name": "WebServers"}


def test_delete_inverts_to_add_from_snapshot():
    xml = (
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias uuid='u1'><enabled>1</enabled><name>WebServers</name><type>host</type>"
        "<content>10.0.0.1\n10.0.0.2</content><description>web</description></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    op, target, payload = build_inverse(_change("delete", "WebServers", {"name": "WebServers"}), xml)
    assert op == "add"
    assert target == "WebServers"
    assert payload["name"] == "WebServers"
    assert payload["type"] == "host"
    assert payload["content"] == "10.0.0.1\n10.0.0.2"


def test_set_inverts_to_set_previous_from_snapshot():
    xml = (
        "<opnsense><OPNsense><Firewall><Alias><aliases>"
        "<alias uuid='u1'><name>WebServers</name><type>host</type><content>1.1.1.1</content></alias>"
        "</aliases></Alias></Firewall></OPNsense></opnsense>"
    )
    op, target, payload = build_inverse(_change("set", "WebServers", {"name": "WebServers", "content": "2.2.2.2"}), xml)
    assert op == "set"
    assert payload["content"] == "1.1.1.1"


def test_delete_without_snapshot_raises():
    with pytest.raises(NoInverseError):
        build_inverse(_change("delete", "WebServers", {"name": "WebServers"}), None)


def test_unknown_kind_raises():
    c = _change("add", "x", {})
    c.kind = "firewall_rule"
    with pytest.raises(NoInverseError):
        build_inverse(c, None)


def test_alias_from_config_xml_missing_returns_none():
    xml = "<opnsense><OPNsense><Firewall><Alias><aliases></aliases></Alias></Firewall></OPNsense></opnsense>"
    assert alias_from_config_xml(xml, "nope") is None


# --- opnsense_setting inverse ---

_IDS_XML = (
    "<opnsense><OPNsense><IDS version='1.0'><general>"
    "<enabled>0</enabled><ips>0</ips><homenet>192.168.0.0/16</homenet>"
    "</general></IDS></OPNsense></opnsense>"
)


def test_setting_from_config_xml_reads_nested_values():
    prev = setting_from_config_xml(_IDS_XML, "OPNsense/IDS", ["general.enabled", "general.homenet"])
    assert prev == {"general.enabled": "0", "general.homenet": "192.168.0.0/16"}


def test_setting_from_config_xml_missing_field_is_empty():
    prev = setting_from_config_xml(_IDS_XML, "OPNsense/IDS", ["general.nope"])
    assert prev == {"general.nope": ""}


def test_setting_set_inverts_to_previous_values_only_for_changed_keys():
    change = _setting_change("ids_general", {
        "endpoint_key": "ids_general",
        "payload": {"general.enabled": "1", "general.homenet": "10.0.0.0/8"},
    })
    op, target, payload = build_inverse(change, _IDS_XML)
    assert op == "set"
    assert target == "ids_general"
    assert payload == {
        "endpoint_key": "ids_general",
        "payload": {"general.enabled": "0", "general.homenet": "192.168.0.0/16"},
    }
    # 'ips' was not in the change -> must not appear in the inverse.
    assert "general.ips" not in payload["payload"]


def test_setting_unknown_endpoint_raises():
    change = _setting_change("nope_endpoint", {"endpoint_key": "nope_endpoint", "payload": {"a.b": "1"}})
    with pytest.raises(NoInverseError):
        build_inverse(change, _IDS_XML)


def test_setting_without_snapshot_raises():
    change = _setting_change("ids_general", {"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}})
    with pytest.raises(NoInverseError):
        build_inverse(change, None)


def test_setting_empty_payload_raises():
    change = _setting_change("ids_general", {"endpoint_key": "ids_general", "payload": {}})
    with pytest.raises(NoInverseError):
        build_inverse(change, _IDS_XML)

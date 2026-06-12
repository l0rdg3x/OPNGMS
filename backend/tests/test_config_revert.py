import uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import (
    NoInverseError,
    alias_from_config_xml,
    build_inverse,
    has_inverse,
)


def _change(operation, target, payload):
    return ConfigChange(tenant_id=uuid.uuid4(), device_id=uuid.uuid4(), created_by=uuid.uuid4(),
                        kind="alias", operation=operation, target=target, payload=payload,
                        baseline_hash="", status="applied")


def test_has_inverse():
    assert has_inverse("alias") is True
    assert has_inverse("opnsense_setting") is False


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
    c.kind = "opnsense_setting"
    with pytest.raises(NoInverseError):
        build_inverse(c, None)


def test_alias_from_config_xml_missing_returns_none():
    xml = "<opnsense><OPNsense><Firewall><Alias><aliases></aliases></Alias></Firewall></OPNsense></opnsense>"
    assert alias_from_config_xml(xml, "nope") is None

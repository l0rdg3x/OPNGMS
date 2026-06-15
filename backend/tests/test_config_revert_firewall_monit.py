import uuid as _uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import NoInverseError, build_inverse, has_inverse

_XML = """<opnsense>
  <OPNsense>
    <Firewall><Filter><rules>
      <rule uuid="r1"><description>tpl-rule</description><interface>lan</interface>
        <action>pass</action><direction>in</direction><ipprotocol>inet</ipprotocol>
        <source_net>any</source_net><destination_net>any</destination_net></rule>
    </rules></Filter></Firewall>
    <monit><test uuid="t1"><name>tpl-test</name><type>SystemResource</type>
      <condition>cpu usage is greater than 90%</condition><action>alert</action><path></path></test></monit>
  </OPNsense>
</opnsense>"""


def _change(kind, target, payload, op="set", status="applied"):
    c = ConfigChange()
    c.id = _uuid.uuid4()
    c.kind = kind
    c.target = target
    c.payload = payload
    c.operation = op
    c.status = status
    return c


def test_has_inverse_for_new_kinds():
    assert has_inverse("firewall_rule") and has_inverse("monit_test")


def test_firewall_rule_set_restore():
    ch = _change("firewall_rule", "tpl-rule", {"description": "tpl-rule", "interface": "lan"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "set" and target == "tpl-rule"
    assert payload["action"] == "pass" and payload["interface"] == "lan"


def test_firewall_rule_created_is_deleted():
    ch = _change("firewall_rule", "ghost", {"description": "ghost", "interface": "lan"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "delete" and payload == {"description": "ghost", "interface": "lan"}


def test_monit_test_set_restore():
    ch = _change("monit_test", "tpl-test", {"name": "tpl-test"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "set" and payload["type"] == "SystemResource" and payload["condition"]


def test_monit_test_created_is_deleted():
    ch = _change("monit_test", "ghost", {"name": "ghost"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "delete" and payload == {"name": "ghost"}


@pytest.mark.parametrize("kind,target,payload", [
    ("firewall_rule", "tpl-rule", {"description": "tpl-rule", "interface": "lan"}),
    ("monit_test", "tpl-test", {"name": "tpl-test"}),
])
def test_no_snapshot_raises(kind, target, payload):
    with pytest.raises(NoInverseError):
        build_inverse(_change(kind, target, payload), None)

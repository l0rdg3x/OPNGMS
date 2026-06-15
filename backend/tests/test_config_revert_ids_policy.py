import uuid as _uuid

import pytest

from app.models.config_change import ConfigChange
from app.services.config_revert import NoInverseError, build_inverse, has_inverse

# One IDS ruleset file (uuid="fA" -> et.rules) and one policy (description=p1) that references it by
# its file-uuid in <rulesets>. The inverse must map the uuid back to the filename and rebuild the
# portable body shape that apply_ids_policy expects (action as a list, content as a dict).
_XML = """<opnsense>
  <OPNsense>
    <IDS>
      <files>
        <file uuid="fA"><filename>et.rules</filename></file>
      </files>
      <policies>
        <policy uuid="p1uuid">
          <enabled>1</enabled>
          <prio>0</prio>
          <description>p1</description>
          <rulesets>fA</rulesets>
          <action>alert,drop</action>
          <new_action>drop</new_action>
          <content>{"severity":["1"]}</content>
        </policy>
      </policies>
    </IDS>
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


def test_has_inverse_for_ids_policy():
    assert has_inverse("ids_policy")


def test_ids_policy_set_restore():
    ch = _change("ids_policy", "p1", {"description": "p1"})
    op, target, body = build_inverse(ch, _XML)
    assert op == "set" and target == "p1"
    assert body["description"] == "p1"
    assert body["enabled"] == "1"
    assert body["prio"] == "0"
    assert body["new_action"] == "drop"
    assert body["action"] == ["alert", "drop"]
    assert body["rulesets"] == ["et.rules"]  # uuid fA -> filename et.rules
    assert body["content"] == {"severity": ["1"]}


def test_ids_policy_created_is_deleted():
    ch = _change("ids_policy", "ghost", {"description": "ghost"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "delete" and target == "ghost"
    assert payload == {"description": "ghost"}


def test_ids_policy_add_inverts_to_delete_without_snapshot():
    ch = _change("ids_policy", "p1", {"description": "p1"}, op="add")
    op, target, payload = build_inverse(ch, None)
    assert op == "delete" and target == "p1"
    assert payload == {"description": "p1"}


def test_ids_policy_no_snapshot_raises():
    # op defaults to "set" -> a set-restore genuinely needs the snapshot.
    with pytest.raises(NoInverseError):
        build_inverse(_change("ids_policy", "p1", {"description": "p1"}), None)

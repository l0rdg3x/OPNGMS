import pytest

import app.services.firewall_rule_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {"description": "block-telnet", "action": "block", "direction": "in",
         "ipprotocol": "inet", "source_net": "any", "destination_net": "any",
         "destination_port": "23", "log": "1"}


def test_firewall_rule_kind_registered():
    spec = tpl.TEMPLATE_KINDS["firewall_rule"]
    assert spec.change_kind == "firewall_rule"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "block-telnet" and payload["action"] == "block"


def test_bind_injects_interface():
    assert tpl.apply_bindings("firewall_rule", dict(_GOOD), {"interface": "wan"})["interface"] == "wan"
    assert tpl.apply_bindings("firewall_rule", dict(_GOOD), {})["interface"] == ""  # floating


def test_validate_accepts_good():
    tpl.validate_body("firewall_rule", _GOOD)


@pytest.mark.parametrize("patch", [
    {"description": ""},                       # identity required
    {"action": "allow"},                       # bad action
    {"direction": "sideways"},                 # bad direction
    {"ipprotocol": "ipx"},                     # bad ipprotocol
    {"source_net": "1.2.3.4 OR 1=1"},          # bad net (space/injection)
    {"destination_port": "ssh; rm -rf"},       # bad port
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("firewall_rule", {**_GOOD, **patch})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_firewall_rule(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "firewall_rule", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True

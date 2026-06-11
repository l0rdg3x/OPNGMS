import pytest

import app.services.ids_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl


def test_suricata_ruleset_kind_registered():
    spec = tpl.TEMPLATE_KINDS["suricata_ruleset"]
    assert spec.change_kind == "ids_rulesets"
    op, target, payload = spec.to_change({"rulesets": ["a.rules"]})
    assert op == "set" and target == "ids_rulesets" and payload["rulesets"] == ["a.rules"]


def test_validate_accepts_good_list():
    tpl.validate_body("suricata_ruleset", {"rulesets": ["abuse.ch.urlhaus.rules", "et.rules"]})


@pytest.mark.parametrize("body", [
    {"rulesets": []},                       # empty
    {"rulesets": "a.rules"},                # not a list
    {"rulesets": [123]},                    # not strings
    {"rulesets": ["../etc/passwd"]},        # bad charset
    {},                                     # missing
])
def test_validate_rejects_bad(body):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("suricata_ruleset", body)


async def test_applier_dispatches_to_apply_ids_rulesets():
    calls = {}

    class FakeClient:
        async def apply_ids_rulesets(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "enabled": payload["rulesets"]}

    await ca.apply_for_kind(
        FakeClient(), "ids_rulesets", "set", {"rulesets": ["a.rules"]}, dry_run=False)
    operation, payload, dry = calls["args"]
    assert operation == "set" and payload == {"rulesets": ["a.rules"]} and dry is False

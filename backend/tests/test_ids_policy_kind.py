import pytest

import app.services.ids_policy_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {
    "description": "Drop ET malware", "enabled": "1", "prio": "0",
    "action": ["alert", "drop"], "rulesets": ["abuse.ch.feodotracker.rules"],
    "content": {"severity": ["1", "2"]}, "new_action": "drop",
}


def test_ids_policy_kind_registered():
    spec = tpl.TEMPLATE_KINDS["ids_policy"]
    assert spec.change_kind == "ids_policy"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "Drop ET malware" and payload["new_action"] == "drop"
    assert spec.pinned == ("description",)


def test_validate_accepts_good():
    tpl.validate_body("ids_policy", _GOOD)


def test_validate_accepts_minimal():
    tpl.validate_body("ids_policy", {"description": "p", "new_action": "alert"})


@pytest.mark.parametrize("patch", [
    {"description": ""},                 # identity required
    {"enabled": "yes"},                  # bad enabled
    {"prio": "high"},                    # non-int prio
    {"action": ["nope"]},                # bad action member
    {"action": "alert"},                 # action not a list
    {"new_action": "explode"},           # bad new_action
    {"rulesets": ["../etc/passwd"]},     # bad ruleset filename
    {"content": {"severity": "1"}},      # content value not a list
    {"content": [1, 2]},                 # content not a dict
    {"content": {"bad key!": ["1"]}},    # content key bad charset
    {"description": "  Drop ET  "},      # leading/trailing whitespace in the identity
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("ids_policy", {**_GOOD, **patch})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_ids_policy(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "ids_policy", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True

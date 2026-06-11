import pytest

import app.services.monit_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {"name": "CPUHigh", "type": "SystemResource",
         "condition": "cpu usage is greater than 90%", "action": "alert", "path": ""}


def test_monit_test_kind_registered():
    spec = tpl.TEMPLATE_KINDS["monit_test"]
    assert spec.change_kind == "monit_test"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "CPUHigh" and payload["action"] == "alert"


def test_validate_accepts_good():
    tpl.validate_body("monit_test", _GOOD)


@pytest.mark.parametrize("patch", [
    {"name": ""},                 # identity required
    {"action": "nope"},           # bad action
    {"condition": ""},            # condition required
    {"type": ""},                 # type required
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("monit_test", {**_GOOD, **patch})


def test_validate_accepts_attach_flag():
    tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "1"})
    tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "0"})


def test_validate_rejects_bad_attach_flag():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("monit_test", {**_GOOD, "attach_to_system": "yes"})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_monit_test(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "monit_test", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True

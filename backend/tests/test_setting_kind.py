import pytest

from app.services import templates as tpl
from app.services import config_apply as ca
import app.services.setting_kind  # noqa: F401  (registers on import)


def test_opnsense_setting_kind_registered():
    spec = tpl.TEMPLATE_KINDS["opnsense_setting"]
    assert spec.change_kind == "opnsense_setting"
    op, target, payload = spec.to_change({"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}})
    assert op == "set" and target == "ids_general" and payload["endpoint_key"] == "ids_general"


def test_validate_rejects_unknown_endpoint():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("opnsense_setting", {"endpoint_key": "nope", "payload": {}})


async def test_applier_dispatches_to_apply_setting():
    calls = {}

    class FakeClient:
        async def apply_setting(self, set_path, reconfigure_path, model_root, payload, *, dry_run):
            calls["args"] = (set_path, reconfigure_path, model_root, payload, dry_run)
            return {"dry_run": dry_run, "result": "ok"}

    res = await ca.apply_for_kind(
        FakeClient(), "opnsense_setting", "set",
        {"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}}, dry_run=True)
    set_path, rec_path, root, payload, dry = calls["args"]
    assert set_path == "ids/settings/set" and rec_path == "ids/service/reconfigure" and root == "ids"
    assert payload == {"general.enabled": "1"} and dry is True

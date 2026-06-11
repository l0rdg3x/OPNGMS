from app.connectors.opnsense.setting_endpoints import SettingEndpoint
from app.services.setting_introspect import infer_fields

EP = SettingEndpoint(
    key="t", label="T", get_path="m/c/get", set_path="m/c/set", reconfigure_path="m/s/reconfigure",
    model_root="m", multi_fields=("g.multi",), exclude_fields=("g.hw",))


def _schema_for(model):
    return {f["path"]: f for f in infer_fields({"m": model}, EP)}


def test_infers_controls_and_skips_excluded_and_lists():
    model = {"g": {
        "enabled": "0",                                                  # switch
        "mode": {"a": {"value": "A", "selected": 1}, "b": {"value": "B", "selected": 0}},  # select
        "multi": {"x": {"value": "X", "selected": 1}, "y": {"value": "Y", "selected": 0}}, # multiselect (hint)
        "many": {"p": {"value": "P", "selected": 1}, "q": {"value": "Q", "selected": 1}},  # multiselect (>=2)
        "name": "hello",                                                 # text
        "hw": {"wan": {"value": "WAN", "selected": 1}},                  # EXCLUDED
        "rules": [1, 2, 3],                                              # list -> skipped
    }}
    s = _schema_for(model)
    assert s["g.enabled"]["control"] == "switch" and s["g.enabled"]["value"] == "0"
    assert s["g.mode"]["control"] == "select" and s["g.mode"]["value"] == "a"
    assert {o["value"] for o in s["g.mode"]["options"]} == {"a", "b"}
    assert s["g.multi"]["control"] == "multiselect" and s["g.multi"]["value"] == ["x"]
    assert s["g.many"]["control"] == "multiselect" and set(s["g.many"]["value"]) == {"p", "q"}
    assert s["g.name"]["control"] == "text" and s["g.name"]["value"] == "hello"
    assert "g.hw" not in s          # excluded
    assert "g.rules" not in s       # list skipped

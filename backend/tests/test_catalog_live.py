from app.services.catalog_live import flatten_values

_MODEL = {
    "model_root": "unbound",
    "fields": [
        {"path": "general.enabled", "type": "bool"},
        {"path": "general.port", "type": "int"},
        {"path": "general.dnssec", "type": "multienum"},
    ],
}


def test_flatten_scalars_and_option_dicts():
    get_response = {"unbound": {"general": {
        "enabled": "1",
        "port": "53",
        "dnssec": {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}},
    }}}
    out = flatten_values(get_response, _MODEL)
    assert out["general.enabled"] == "1"
    assert out["general.port"] == "53"
    assert out["general.dnssec"] == ["a"]  # multi-select -> selected keys


def test_flatten_missing_model_root_is_empty():
    assert flatten_values({}, _MODEL) == {}

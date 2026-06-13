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


from app.services.catalog_live import extract_grid_rows

_GRID = {"path": "hosts", "fields": [{"path": "hostname", "type": "string"},
                                     {"path": "rr", "type": "enum"}]}


def test_extract_grid_rows_uuid_keyed_with_option_cell():
    get_response = {"unbound": {"hosts": {
        "ab-12": {"hostname": "web", "rr": {"A": {"value": "A", "selected": "1"}}},
        "cd-34": {"hostname": "db", "rr": {"A": {"value": "A", "selected": "0"}}},
    }}}
    rows = extract_grid_rows(get_response, _MODEL, _GRID)
    assert {"uuid": "ab-12", "hostname": "web", "rr": ["A"]} in rows
    assert {"uuid": "cd-34", "hostname": "db", "rr": []} in rows


def test_extract_grid_rows_missing_node_is_empty():
    assert extract_grid_rows({}, _MODEL, _GRID) == []


from app.services.catalog_live import extract_options

_REF_MODEL = {
    "model_root": "unbound",
    "fields": [
        {"path": "general.outgoing", "type": "ref"},
        {"path": "general.port", "type": "int"},
    ],
}


def test_extract_options_returns_choices_for_option_dict_fields():
    get_response = {"unbound": {"general": {
        "outgoing": {"lan": {"value": "LAN", "selected": "1"}, "wan": {"value": "WAN", "selected": "0"}},
        "port": "53",
    }}}
    opts = extract_options(get_response, _REF_MODEL)
    assert opts["general.outgoing"] == [{"value": "lan", "label": "LAN"}, {"value": "wan", "label": "WAN"}]
    assert "general.port" not in opts  # plain string -> no options


from app.services.catalog_live import extract_grid_options

_GRID_OPT = {"path": "hosts", "fields": [{"path": "rr", "type": "enum"}, {"path": "hostname", "type": "string"}]}


def test_extract_grid_options_returns_cell_choices():
    get_response = {"unbound": {"hosts": {
        "ab-12": {"rr": {"A": {"value": "A", "selected": "1"}, "AAAA": {"value": "AAAA", "selected": "0"}},
                  "hostname": "web"},
    }}}
    out = extract_grid_options(get_response, _MODEL, _GRID_OPT)
    assert out["rr"] == [{"value": "A", "label": "A"}, {"value": "AAAA", "label": "AAAA"}]
    assert "hostname" not in out  # plain string cell -> no options

from tools.opnsense_catalog.types import Field, Grid, Model, model_to_dict


def test_field_defaults_and_dict():
    f = Field(path="general.enabled", type="bool")
    assert f.required is False and f.confidence == "rich" and f.options == []


def test_model_to_dict_is_stable_and_sorted():
    m = Model(
        id="ids.general", title="IDS", source="core", model_root="ids", xml_path="OPNsense/IDS",
        endpoints={"set": "ids/settings/set", "get": "ids/settings/get"},
        fields=[Field(path="general.ips", type="bool"), Field(path="general.enabled", type="bool")],
        grids=[Grid(path="userrules", endpoints={"add": "ids/settings/addUserrule"},
                    fields=[Field(path="enabled", type="bool")])],
        pages=[{"id": "general", "label": "General", "fields": ["general.enabled"]}],
    )
    d = model_to_dict(m)
    assert list(d["endpoints"]) == ["get", "set"]
    assert [f["path"] for f in d["fields"]] == ["general.enabled", "general.ips"]
    assert d["grids"][0]["path"] == "userrules"
    assert "options" not in d["fields"][0]


def test_model_to_dict_emits_plugin_block_when_present():
    core = model_to_dict(Model(id="ids", title="IDS", source="core", model_root="ids",
                               xml_path="OPNsense/IDS"))
    assert "plugin" not in core  # core models carry no plugin block

    plug = model_to_dict(Model(id="haproxy", title="HAProxy", source="plugins",
                               model_root="haproxy", xml_path="OPNsense/HAProxy/general",
                               plugin={"package": "os-haproxy", "title": "HAProxy",
                                       "category": "net", "version": "5.1"}))
    assert plug["source"] == "plugins"
    assert plug["plugin"] == {"package": "os-haproxy", "title": "HAProxy",
                              "category": "net", "version": "5.1"}

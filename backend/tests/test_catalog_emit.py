from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.types import Field, Grid, ParsedModel


def _parsed():
    return ParsedModel(mount="//OPNsense/IDS",
                       fields=[Field(path="general.enabled", type="bool"),
                               Field(path="general.x", type="string", confidence="raw")],
                       grids=[Grid(path="rules.rule", fields=[Field(path="enabled", type="bool")])])


def test_colliding_module_ids_are_disambiguated_not_overwritten():
    # Two models in one module (e.g. OpenVPN Instances + StaticKey) must both survive (never-drop).
    a = assemble_model("OpenVPN", ParsedModel(mount="//OPNsense/OpenVPN/Instances"), {}, {}, {}, source="core")
    b = assemble_model("OpenVPN", ParsedModel(mount="//OPNsense/OpenVPN/StaticKey"), {}, {}, {}, source="core")
    cat = build_catalog([a, b], edition="community", version="x", generated_from={})
    assert set(cat["models"]) == {"openvpn.instances", "openvpn.statickey"}


def test_assemble_merges_labels_endpoints_and_derives_ids():
    forms = {"general.enabled": {"label": "Enabled", "help": "h", "page": "general"}}
    eps = {"get": "ids/settings/get"}
    grid_eps = {"rules.rule": {"add": "ids/settings/addRule"}}
    m = assemble_model("IDS", _parsed(), forms, eps, grid_eps, source="core")
    assert m.id == "ids" and m.model_root == "ids" and m.xml_path == "OPNsense/IDS"
    assert next(f for f in m.fields if f.path == "general.enabled").label == "Enabled"
    assert m.grids[0].endpoints == {"add": "ids/settings/addRule"}


def test_build_catalog_and_coverage():
    m = assemble_model("IDS", _parsed(), {}, {"get": "ids/settings/get"}, {}, source="core")
    cat = build_catalog([m], edition="community", version="26.1.8",
                        generated_from={"core": "26.1.8"})
    assert cat["edition"] == "community" and cat["version"] == "26.1.8"
    assert "ids" in cat["models"]
    rep = coverage_report(cat)
    # 2 scalar fields + 1 grid field = 3 total (coverage counts grid fields too); 1 raw.
    assert rep["models"] == 1 and rep["fields_total"] == 3 and rep["fields_raw"] == 1

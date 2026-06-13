from app.services.config_map import annotate_with_catalog

# build_tree-shaped node: {tag, path, attributes, children, [value]}
TREE = {
    "tag": "opnsense", "path": "opnsense", "attributes": {}, "children": [
        {"tag": "unboundplus", "path": "opnsense/unboundplus", "attributes": {}, "children": [
            {"tag": "general", "path": "opnsense/unboundplus/general", "attributes": {}, "children": []},
        ]},
        {"tag": "legacything", "path": "opnsense/legacything", "attributes": {}, "children": []},
    ],
}
CATALOG = {"models": {"unbound.x": {"xml_path": "OPNsense/unboundplus"}}}


def test_nodes_under_a_model_xml_path_are_editable():
    out = annotate_with_catalog(TREE, CATALOG)
    unbound = out["children"][0]
    assert unbound["editable"] is True and unbound["catalog_model_id"] == "unbound.x"
    assert out["children"][0]["children"][0]["editable"] is True  # subtree inherits
    assert out["children"][1]["editable"] is False  # legacything → read-only
    assert "catalog_model_id" not in out["children"][1]


def test_index_suffixed_paths_match_on_tag_prefix():
    tree = {"tag": "opnsense", "path": "opnsense", "attributes": {}, "children": [
        {"tag": "unboundplus", "path": "opnsense/unboundplus[1]", "attributes": {}, "children": []},
    ]}
    out = annotate_with_catalog(tree, {"models": {"u": {"xml_path": "OPNsense/unboundplus"}}})
    assert out["children"][0]["editable"] is True

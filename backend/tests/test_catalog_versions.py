from app.services.catalog_versions import diff_catalogs


def _cat(models):
    return {"models": models}


def test_diff_added_removed_changed_models_and_fields():
    a = _cat({
        "m.keep": {"fields": [{"path": "a", "type": "string"}, {"path": "b", "type": "string"}]},
        "m.gone": {"fields": [{"path": "x", "type": "string"}]},
    })
    b = _cat({
        "m.keep": {"fields": [
            {"path": "a", "type": "boolean"},          # changed (type)
            {"path": "c", "type": "string"},           # added
        ]},                                            # 'b' removed
        "m.new": {"fields": [{"path": "y", "type": "string"}]},  # added model
    })
    d = diff_catalogs(a, b)
    assert d["added_models"] == ["m.new"]
    assert d["removed_models"] == ["m.gone"]
    mk = d["models"]["m.keep"]
    assert mk["added_fields"] == ["c"]
    assert mk["removed_fields"] == ["b"]
    assert mk["changed_fields"] == ["a"]


def test_diff_identical_is_empty():
    a = _cat({"m": {"fields": [{"path": "a", "type": "string", "required": True}]}})
    d = diff_catalogs(a, a)
    assert d["added_models"] == [] and d["removed_models"] == []
    assert d["models"] == {}

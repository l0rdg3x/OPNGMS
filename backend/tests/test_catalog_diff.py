from tools.opnsense_catalog.diff import diff_catalogs


def _cat(models):
    return {"edition": "community", "version": "x", "models": models}


def _model(fields):
    return {"fields": [{"path": p, "type": t} for p, t in fields]}


def test_diff_reports_added_removed_and_changed():
    a = _cat({"ids": _model([("general.enabled", "bool"), ("general.old", "string")]),
              "gone": _model([])})
    b = _cat({"ids": _model([("general.enabled", "int"), ("general.new", "bool")]),
              "added": _model([])})
    d = diff_catalogs(a, b)
    assert d["added_models"] == ["added"]
    assert d["removed_models"] == ["gone"]
    ids = d["models"]["ids"]
    assert ids["added_fields"] == ["general.new"]
    assert ids["removed_fields"] == ["general.old"]
    assert ids["changed_fields"] == [{"path": "general.enabled", "attr": "type",
                                       "before": "bool", "after": "int"}]

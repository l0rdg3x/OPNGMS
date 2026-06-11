from app.services.monit_introspect import infer_test_fields

_MODEL = {"test": {
    "name": "",
    "type": {"SystemResource": {"value": "SystemResource", "selected": 1},
             "Existence": {"value": "Existence", "selected": 0}},
    "condition": "",
    "action": {"alert": {"value": "alert", "selected": 0}},
    "path": "",
}}


def test_infer_test_fields_classifies_controls():
    out = infer_test_fields(_MODEL)
    paths = {f["path"]: f["control"] for f in out["fields"]}
    assert paths["type"] == "select" and paths["action"] == "select"
    assert paths["name"] == "text" and paths["condition"] == "text" and paths["path"] == "text"

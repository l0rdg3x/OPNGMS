"""Infer a value-controlled field schema from the Monit blank test model (monit/settings/getTest).

Reuses the setting-introspection classifiers; the flat `test` model is walked once (option-objects ->
select; plain strings -> text). No exclusions (a monit test is fully fleet-portable)."""
from app.services.setting_introspect import _is_option_dict, _options, _selected


def infer_test_fields(get_test_response: dict) -> dict:
    model = (get_test_response or {}).get("test", {})
    fields: list[dict] = []
    for key, val in model.items():
        if _is_option_dict(val):
            sel = _selected(val)
            fields.append({"path": key, "label": key, "control": "select",
                           "options": _options(val), "value": sel[0] if sel else ""})
        elif isinstance(val, str):
            fields.append({"path": key, "label": key, "control": "text", "value": val})
        # lists / other -> skipped
    return {"fields": fields}

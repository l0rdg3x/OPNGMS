"""Infer a value-controlled field schema from the Rules[new] blank rule model (firewall/filter/getRule).

Reuses the setting-introspection classifiers. The flat `rule` model is walked once: device-specific
reference fields and computed/display-mirror fields are excluded; the `interface` field's options are
surfaced separately (they power the apply-time interface picker, not a template body field)."""
from app.services.setting_introspect import _is_option_dict, _options, _selected

# Device-specific references / computed fields that must NOT be templated (not fleet-portable).
_EXCLUDE = {
    "interface", "gateway", "replyto", "divert-to", "categories", "sched",
    "shaper1", "shaper2", "sort_order", "prio_group",
}


def infer_rule_fields(get_rule_response: dict) -> dict:
    model = (get_rule_response or {}).get("rule", {})
    fields: list[dict] = []
    interfaces: list[dict] = []
    for key, val in model.items():
        if key == "interface" and _is_option_dict(val):
            interfaces = _options(val)
            continue
        if key in _EXCLUDE or key.startswith("%"):
            continue
        if _is_option_dict(val):
            sel = _selected(val)
            multi = len(sel) >= 2
            fields.append({"path": key, "label": key,
                           "control": "multiselect" if multi else "select",
                           "options": _options(val),
                           "value": sel if multi else (sel[0] if sel else "")})
        elif isinstance(val, str) and val in ("0", "1"):
            fields.append({"path": key, "label": key, "control": "switch", "value": val})
        elif isinstance(val, str):
            fields.append({"path": key, "label": key, "control": "text", "value": val})
        # lists / other -> skipped
    return {"fields": fields, "interfaces": interfaces}

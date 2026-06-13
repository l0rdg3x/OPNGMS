"""Infer a value-controlled field schema from an OPNsense model `get` response.

Heuristic (precise for the common shapes; OPNsense's own `set` validation is the final backstop):
option-dict -> select / multiselect; "0"|"1" -> switch; plain string -> text; nested object ->
recurse (dotted path). Fields in the endpoint's `exclude_fields` (hardware/device-specific) and
non-dict/str leaves (lists) are skipped."""
from app.connectors.opnsense.setting_endpoints import SettingEndpoint
from app.services.opnsense_values import is_option_dict, options, selected


def infer_fields(get_response: dict, endpoint: SettingEndpoint) -> list[dict]:
    model = (get_response or {}).get(endpoint.model_root, {})
    out: list[dict] = []
    _walk(model, "", endpoint, out)
    return out


def _walk(node: dict, prefix: str, ep: SettingEndpoint, out: list[dict]) -> None:
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in ep.exclude_fields:
            continue
        if is_option_dict(val):
            sel = selected(val)
            multi = len(sel) >= 2 or path in ep.multi_fields
            out.append({"path": path, "label": key,
                        "control": "multiselect" if multi else "select",
                        "options": options(val),
                        "value": sel if multi else (sel[0] if sel else "")})
        elif isinstance(val, str) and val in ("0", "1"):
            out.append({"path": path, "label": key, "control": "switch", "value": val})
        elif isinstance(val, str):
            out.append({"path": path, "label": key, "control": "text", "value": val})
        elif isinstance(val, dict):
            _walk(val, path, ep, out)   # nested object -> recurse
        # else (list / other) -> skipped

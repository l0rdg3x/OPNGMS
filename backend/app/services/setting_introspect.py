"""Infer a value-controlled field schema from an OPNsense model `get` response.

Heuristic (precise for the common shapes; OPNsense's own `set` validation is the final backstop):
option-dict -> select / multiselect; "0"|"1" -> switch; plain string -> text; nested object ->
recurse (dotted path). Fields in the endpoint's `exclude_fields` (hardware/device-specific) and
non-dict/str leaves (lists) are skipped."""
from app.connectors.opnsense.setting_endpoints import SettingEndpoint


def _is_option_dict(v) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(
        isinstance(o, dict) and "selected" in o for o in v.values())


def _options(v: dict) -> list[dict]:
    return [{"value": k, "label": str(o.get("value", k))} for k, o in v.items()]


def _selected(v: dict) -> list[str]:
    return [k for k, o in v.items() if str(o.get("selected")) == "1"]


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
        if _is_option_dict(val):
            sel = _selected(val)
            multi = len(sel) >= 2 or path in ep.multi_fields
            out.append({"path": path, "label": key,
                        "control": "multiselect" if multi else "select",
                        "options": _options(val),
                        "value": sel if multi else (sel[0] if sel else "")})
        elif isinstance(val, str) and val in ("0", "1"):
            out.append({"path": path, "label": key, "control": "switch", "value": val})
        elif isinstance(val, str):
            out.append({"path": path, "label": key, "control": "text", "value": val})
        elif isinstance(val, dict):
            _walk(val, path, ep, out)   # nested object -> recurse
        # else (list / other) -> skipped

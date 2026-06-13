"""Merge a catalog model's schema with the device's LIVE values (from its `<model>/settings/get`).

Pure + device-independent: the API layer fetches the raw `get` response and hands it here. Option/enum
dicts are normalized to their selected key(s); grid (uuid-keyed) nodes are returned as row lists.
"""
from app.services.opnsense_values import is_option_dict, selected


def _scalar(value) -> str | list[str] | None:
    """A leaf's current value: option-dict -> selected key(s); plain string -> itself; else None."""
    if is_option_dict(value):
        return selected(value)
    if isinstance(value, str):
        return value
    return None


def flatten_values(get_response: dict, model: dict) -> dict[str, str | list[str]]:
    """{dotted_path: current_value} for the model's scalar leaves (grids handled separately)."""
    root = (get_response or {}).get(model.get("model_root", ""), {})
    out: dict[str, str | list[str]] = {}

    def walk(node, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for key, val in node.items():
            path = f"{prefix}.{key}" if prefix else key
            leaf = _scalar(val)
            if leaf is not None:
                out[path] = leaf
            elif isinstance(val, dict):
                walk(val, path)  # nested object -> recurse (grids are filtered out below)

    walk(root, "")
    # Keep only paths the catalog declares as scalar fields (drops grid nodes + unknown extras).
    field_paths = {f["path"] for f in model.get("fields", [])}
    return {p: v for p, v in out.items() if p in field_paths}

"""Shared normalization of OPNsense model `get` responses.

OPNsense renders option/enum fields as a dict of {key: {"value": <label>, "selected": "0"|"1"}}.
These helpers turn that into options + the selected key(s). Used by the introspection form builder
and the catalog editor's live-value flattener.
"""


def is_option_dict(v) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(
        isinstance(o, dict) and "selected" in o for o in v.values())


def options(v: dict) -> list[dict]:
    return [{"value": k, "label": str(o.get("value", k))} for k, o in v.items()]


def selected(v: dict) -> list[str]:
    return [k for k, o in v.items() if str(o.get("selected")) == "1"]

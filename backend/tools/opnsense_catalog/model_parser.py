from __future__ import annotations

from defusedxml import ElementTree as DET

from tools.opnsense_catalog.types import Field, ParsedModel

# OPNsense field class -> our catalog type. Unknown -> raw string (never-drop).
_TYPE_MAP = {
    "BooleanField": "bool",
    "IntegerField": "int",
    "PortField": "int",
    "TextField": "string",
    "DescriptionField": "string",
    "HostnameField": "string",
    "EmailField": "string",
    "NetworkField": "network",
    "NetworkAliasField": "network",
    "OptionField": "enum",
    "ModelRelationField": "ref",
}


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text if child is not None and child.text is not None else None


def _is_truthy(el, tag: str) -> bool:
    return (_text(el, tag) or "").strip().upper() in ("Y", "YES", "1", "TRUE")


def _options(el) -> list[str]:
    ov = el.find("OptionValues")
    if ov is None:
        return []
    return [(opt.text or opt.tag) for opt in list(ov)]


def _walk(node, prefix: str, fields: list[Field]) -> None:
    for child in list(node):
        tag = child.tag
        path = f"{prefix}.{tag}" if prefix else tag
        cls = child.get("type")
        if cls is None:
            _walk(child, path, fields)              # a container node -> recurse
            continue
        base = _TYPE_MAP.get(cls)
        confidence = "rich" if base is not None else "raw"
        ftype = base or "string"
        if base == "enum" and _is_truthy(child, "Multiple"):
            ftype = "multienum"
        fields.append(Field(
            path=path, type=ftype, required=_is_truthy(child, "Required"),
            default=_text(child, "default"), options=_options(child), confidence=confidence,
        ))


def parse_model(xml_text: str) -> ParsedModel:
    root = DET.fromstring(xml_text)
    mount = (root.findtext("mount") or "").strip()
    items = root.find("items")
    fields: list[Field] = []
    if items is not None:
        _walk(items, "", fields)
    return ParsedModel(mount=mount, fields=fields, grids=[])

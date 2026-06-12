from __future__ import annotations

from defusedxml import ElementTree as DET

from tools.opnsense_catalog.types import Field, ParsedModel

# OPNsense field class -> our catalog type. Unknown -> raw string (never-drop).
_TYPE_MAP = {
    "BooleanField": "bool",
    "IntegerField": "int",
    "PortField": "int",
    "NumericField": "string",       # may hold decimals; keep as text (box validates), not a forced int
    "TextField": "string",
    "DescriptionField": "string",
    "HostnameField": "string",
    "EmailField": "string",
    "CSVListField": "string",       # comma-separated list stored as a string
    "NetworkField": "network",
    "NetworkAliasField": "network",
    "OptionField": "enum",
    "ProtocolField": "enum",        # protocol picker (fixed-ish option set)
    "CountryField": "enum",         # country picker (options resolved live)
    "ModelRelationField": "ref",
    "InterfaceField": "ref",        # reference to a configured interface (options resolved live)
    "CertificateField": "ref",      # reference to a stored certificate
    "AuthGroupField": "ref",
    "AuthenticationServerField": "ref",
    "VirtualIPField": "ref",
    "ConfigdActionsField": "ref",
    "MacAddressField": "string",
    "UrlField": "string",
    "IPPortField": "string",        # host:port
    "JsonKeyValueStoreField": "string",
    "JsonField": "string",
    "Base64Field": "string",
    "UpdateOnlyTextField": "string",
    "LegacyLinkField": "string",
    "AutoNumberField": "int",
}


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text if child is not None and child.text is not None else None


def _is_truthy(el, tag: str) -> bool:
    return (_text(el, tag) or "").strip().upper() in ("Y", "YES", "1", "TRUE")


def _options(el) -> list[str]:
    # The API VALUE of an option is its `value` attribute if present, else the element tag (NOT the
    # text, which is the display label). Mirrors OPNsense's OptionField::getNodeData().
    ov = el.find("OptionValues")
    if ov is None:
        return []
    return [(opt.get("value") if opt.get("value") is not None else opt.tag) for opt in list(ov)]


def _walk(node, prefix: str, fields: list[Field], grids) -> None:
    from tools.opnsense_catalog.types import Grid
    for child in list(node):
        tag = child.tag
        path = f"{prefix}.{tag}" if prefix else tag
        cls = child.get("type")
        if cls == "ArrayField":
            item_fields: list[Field] = []
            # Item scalar fields are relative to the row; a nested ArrayField is appended to the
            # SAME `grids` list (qualified by this grid's path) so it is never silently dropped.
            _walk(child, "", item_fields, grids)
            grids.append(Grid(path=path, fields=item_fields))
            continue
        if cls is None:
            _walk(child, path, fields, grids)
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
    grids: list = []
    if items is not None:
        _walk(items, "", fields, grids)
    return ParsedModel(mount=mount, fields=fields, grids=grids)

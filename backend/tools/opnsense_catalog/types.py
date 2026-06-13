from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Field:
    path: str
    type: str                          # bool|int|string|enum|multienum|network|ref
    required: bool = False
    default: str | None = None
    options: list[str] = field(default_factory=list)
    label: str = ""
    help: str = ""
    confidence: str = "rich"           # rich|raw


@dataclass
class Grid:
    path: str
    endpoints: dict[str, str] = field(default_factory=dict)
    fields: list[Field] = field(default_factory=list)


@dataclass
class Model:
    id: str
    title: str
    source: str
    model_root: str
    xml_path: str
    endpoints: dict[str, str] = field(default_factory=dict)
    fields: list[Field] = field(default_factory=list)
    grids: list[Grid] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    plugin: dict | None = None          # {package, title, category, version} for source=="plugins"


@dataclass
class ParsedModel:
    """What model_parser produces before forms/endpoints are merged in."""
    mount: str                          # e.g. //OPNsense/IDS
    fields: list[Field] = field(default_factory=list)
    grids: list[Grid] = field(default_factory=list)


def _field_to_dict(f: Field) -> dict:
    out: dict = {"path": f.path, "type": f.type, "confidence": f.confidence}
    if f.required:
        out["required"] = True
    if f.default is not None:
        out["default"] = f.default
    if f.options:
        out["options"] = list(f.options)
    if f.label:
        out["label"] = f.label
    if f.help:
        out["help"] = f.help
    return out


def _grid_to_dict(g: Grid) -> dict:
    return {"path": g.path, "endpoints": dict(sorted(g.endpoints.items())),
            "fields": [_field_to_dict(x) for x in sorted(g.fields, key=lambda f: f.path)]}


def model_to_dict(m: Model) -> dict:
    out = {
        "id": m.id, "title": m.title, "source": m.source, "model_root": m.model_root,
        "xml_path": m.xml_path,
        "endpoints": dict(sorted(m.endpoints.items())),
        "pages": m.pages,
        "fields": [_field_to_dict(f) for f in sorted(m.fields, key=lambda f: f.path)],
        "grids": [_grid_to_dict(g) for g in sorted(m.grids, key=lambda g: g.path)],
    }
    if m.plugin is not None:
        out["plugin"] = m.plugin
    return out

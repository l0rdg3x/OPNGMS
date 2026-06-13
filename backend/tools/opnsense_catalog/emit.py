from __future__ import annotations

from tools.opnsense_catalog.types import Field, Model, ParsedModel, model_to_dict


def _label_fields(fields: list[Field], forms: dict[str, dict]) -> list[Field]:
    out: list[Field] = []
    for f in fields:
        meta = forms.get(f.path, {})
        out.append(Field(path=f.path, type=f.type, required=f.required, default=f.default,
                         options=f.options, confidence=f.confidence,
                         label=meta.get("label", "") or f.label,
                         help=meta.get("help", "") or f.help))
    return out


def assemble_model(module: str, parsed: ParsedModel, forms: dict[str, dict],
                   endpoints: dict[str, str], grid_endpoints: dict[str, dict], *, source: str) -> Model:
    # The API base / set-body root come from the MODULE (e.g. "unbound"); the mount only gives the
    # config.xml location, which can differ (Unbound mounts at //OPNsense/unboundplus).
    model_root = module.lower()
    xml_path = parsed.mount.strip("/")
    grids = []
    for g in parsed.grids:
        g.endpoints = grid_endpoints.get(g.path, {})
        g.fields = _label_fields(g.fields, forms)
        grids.append(g)
    pages: dict[str, list[str]] = {}
    for f in parsed.fields:
        # Resolve the page id BEFORE grouping so fields with no form entry ("") merge into the
        # default "general" page instead of producing a duplicate "general" entry.
        page = forms.get(f.path, {}).get("page", "") or "general"
        pages.setdefault(page, []).append(f.path)
    page_list = [{"id": p, "fields": sorted(fs)} for p, fs in sorted(pages.items())]
    return Model(id=model_root, title=module, source=source, model_root=model_root,
                 xml_path=xml_path, endpoints=endpoints,
                 fields=_label_fields(parsed.fields, forms), grids=grids, pages=page_list)


def build_catalog(models: list[Model], *, edition: str, version: str, generated_from: dict) -> dict:
    # A module with >1 model (e.g. OpenVPN: Instances/StaticKey/...) would collide on id=module;
    # qualify the colliding ones with the mount leaf so NO model is silently overwritten (never-drop).
    from collections import Counter
    id_counts = Counter(m.id for m in models)
    out_models: dict[str, dict] = {}
    for m in sorted(models, key=lambda m: (m.id, m.xml_path)):
        key = m.id
        if id_counts[m.id] > 1:
            key = f"{m.id}.{m.xml_path.rstrip('/').split('/')[-1].lower()}"
        out_models[key] = model_to_dict(m)
    return {"edition": edition, "version": version, "generated_from": generated_from, "models": out_models}


def coverage_report(catalog: dict) -> dict:
    total = raw = 0
    for m in catalog["models"].values():
        for fl in [m["fields"], *[g["fields"] for g in m.get("grids", [])]]:  # scalar + grid fields
            for f in fl:
                total += 1
                raw += 1 if f.get("confidence") == "raw" else 0
    leaves = unmapped = 0

    def _walk(nodes):
        nonlocal leaves, unmapped
        for n in nodes:
            if n.get("children"):
                _walk(n["children"])
            elif n.get("url"):
                leaves += 1
                unmapped += 1 if n.get("model_id") is None else 0

    _walk(catalog.get("menu", []))
    return {"models": len(catalog["models"]), "fields_total": total, "fields_raw": raw,
            "menu_leaves": leaves, "menu_unmapped": unmapped}

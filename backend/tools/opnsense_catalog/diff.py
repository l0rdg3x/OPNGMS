from __future__ import annotations


def _fields_by_path(model: dict) -> dict[str, dict]:
    return {f["path"]: f for f in model.get("fields", [])}


def diff_catalogs(a: dict, b: dict) -> dict:
    am, bm = a.get("models", {}), b.get("models", {})
    added_models = sorted(set(bm) - set(am))
    removed_models = sorted(set(am) - set(bm))
    models: dict[str, dict] = {}
    for mid in sorted(set(am) & set(bm)):
        af, bf = _fields_by_path(am[mid]), _fields_by_path(bm[mid])
        added = sorted(set(bf) - set(af))
        removed = sorted(set(af) - set(bf))
        changed = []
        for path in sorted(set(af) & set(bf)):
            for attr in ("type", "required", "default", "options"):
                if af[path].get(attr) != bf[path].get(attr):
                    changed.append({"path": path, "attr": attr,
                                    "before": af[path].get(attr), "after": bf[path].get(attr)})
        if added or removed or changed:
            models[mid] = {"added_fields": added, "removed_fields": removed, "changed_fields": changed}
    return {"added_models": added_models, "removed_models": removed_models, "models": models}

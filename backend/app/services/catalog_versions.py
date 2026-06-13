"""Pure cross-version catalog diff for the editor's "new/changed since X" badges.

Reimplemented app-side (tools/ is offline-only) over the runtime catalog shape: models keyed by id,
each with a `fields` list of {path, type, [required], [default], [options]}."""
from __future__ import annotations

# Field attributes that constitute a "change" when they differ between versions.
_FIELD_ATTRS = ("type", "required", "default", "options")


def _fields_by_path(model: dict) -> dict[str, dict]:
    return {f["path"]: f for f in (model.get("fields") or []) if isinstance(f, dict) and "path" in f}


def _field_changed(a: dict, b: dict) -> bool:
    return any(a.get(k) != b.get(k) for k in _FIELD_ATTRS)


def diff_catalogs(a: dict, b: dict) -> dict:
    """Diff catalog `a` (baseline/from) vs `b` (device/to).

    Returns {added_models, removed_models, models: {mid: {added_fields, removed_fields, changed_fields}}}.
    Only models present in both with field-level differences appear under `models`."""
    ma, mb = a.get("models", {}) or {}, b.get("models", {}) or {}
    added_models = sorted(k for k in mb if k not in ma)
    removed_models = sorted(k for k in ma if k not in mb)
    models: dict[str, dict] = {}
    for mid in mb.keys() & ma.keys():
        fa, fb = _fields_by_path(ma[mid]), _fields_by_path(mb[mid])
        added = sorted(p for p in fb if p not in fa)
        removed = sorted(p for p in fa if p not in fb)
        changed = sorted(p for p in fb.keys() & fa.keys() if _field_changed(fa[p], fb[p]))
        if added or removed or changed:
            models[mid] = {"added_fields": added, "removed_fields": removed, "changed_fields": changed}
    return {"added_models": added_models, "removed_models": removed_models, "models": models}

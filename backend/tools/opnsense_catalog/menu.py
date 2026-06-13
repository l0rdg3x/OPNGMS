from __future__ import annotations

from pathlib import Path

from defusedxml import ElementTree as DET

_ORDER_LAST = 10_000  # nodes without an explicit order sort after those with one


def _node(el) -> dict:
    label = (el.get("VisibleName") or el.tag).strip()
    order = el.get("order")
    node: dict = {"id": el.tag, "label": label,
                  "order": int(order) if order and order.isdigit() else _ORDER_LAST}
    css = el.get("cssClass")
    if css:
        node["icon"] = css.strip()
    url = el.get("url")
    if url:
        node["url"] = url.strip()
    children = [_node(c) for c in list(el)]
    if children:
        node["children"] = children
    return node


def parse_menu(xml_text: str) -> list[dict]:
    """One <menu> fragment -> a list of top-level category nodes (recursive)."""
    root = DET.fromstring(xml_text)
    return [_node(c) for c in list(root)]


def _merge_lists(lists: list[list[dict]]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for nodes in lists:
        for n in nodes:
            if n["id"] not in by_id:
                by_id[n["id"]] = {"id": n["id"], "label": n["id"], "order": _ORDER_LAST}
                order.append(n["id"])
            cur = by_id[n["id"]]
            # Prefer a real VisibleName (label != tag) over a bare-tag label; never clobber.
            if cur["label"] == cur["id"] and n["label"] != n["id"]:
                cur["label"] = n["label"]
            if "icon" not in cur and "icon" in n:
                cur["icon"] = n["icon"]
            if "url" not in cur and "url" in n:
                cur["url"] = n["url"]
            if n.get("order", _ORDER_LAST) < cur["order"]:
                cur["order"] = n["order"]
            cur.setdefault("_kids", []).append(n.get("children", []))
    out = []
    for cid in order:
        node = by_id[cid]
        kids = node.pop("_kids", [])
        merged_kids = _merge_lists(kids)
        if merged_kids:
            node["children"] = merged_kids
        out.append(node)
    out.sort(key=lambda n: (n["order"], n["label"]))
    return out


def merge_menus(fragments: list[list[dict]]) -> list[dict]:
    """Deep-merge parsed fragments into one tree (union children by id, sort by order then label)."""
    return _merge_lists(fragments)


def _resolve_leaf(url: str, model_ids: set[str]) -> str | None:
    """Map a menu leaf `/ui/<controller>/<action>...` to a catalog model id (or None).

    OPNsense menu urls and the catalog's model ids diverge in a few systematic ways, so we normalize
    and try several candidates: the `#anchor`/`?query`/trailing-`*` is stripped; the model id may carry
    a `+` mount-leaf qualifier (`auth.group+` ↔ url `/ui/auth/group`); and the model id's leaf is often
    the PLURAL of the url action (`interfaces.vlans` ↔ `/ui/interfaces/vlan`). Genuinely divergent names
    (kea `v4`≠`dhcp4`, ipsec `connections`) and legacy `.php`/diagnostics pages stay unmapped.
    """
    url = url.split("#", 1)[0].split("?", 1)[0]  # drop anchor + query
    parts = [p.rstrip("*") for p in url.split("/")]
    parts = [p for p in parts if p]              # drop empties + bare '*' wildcard segments
    if len(parts) < 2 or parts[0] != "ui":
        return None
    seg = parts[1:]  # after /ui/
    candidates: list[str] = []
    for i in (1, 2):  # the action is usually seg[1]; some urls nest one deeper (e.g. /ui/kea/dhcp/...)
        if len(seg) > i:
            candidates += [f"{seg[0]}.{seg[i]}", f"{seg[0]}.{seg[i]}s"]  # exact + plural
    candidates.append(seg[0])
    # Allow a trailing `+` qualifier on the model id (e.g. 'auth.group+' matches candidate 'auth.group').
    stripped = {m.rstrip("+"): m for m in model_ids}
    for c in candidates:
        if c in model_ids:
            return c
        if c in stripped:
            return stripped[c]
    return None


def resolve_model_ids(menu: list[dict], model_ids: set[str]) -> list[dict]:
    """Set `model_id` on every leaf (a node with `url` and no children); recurse. Returns the menu."""
    for node in menu:
        if "children" in node:
            resolve_model_ids(node["children"], model_ids)
        elif node.get("url"):
            node["model_id"] = _resolve_leaf(node["url"], model_ids)
    return menu


def discover_menus(root: Path) -> list[Path]:
    """All module Menu.xml files under an extracted source tree."""
    return sorted(root.rglob("mvc/app/models/OPNsense/*/Menu/Menu.xml"))

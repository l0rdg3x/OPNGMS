from __future__ import annotations

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

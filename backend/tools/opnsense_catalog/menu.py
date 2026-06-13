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

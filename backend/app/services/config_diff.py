"""Version/plugin-tolerant config comparison.

Pure functions over config.xml strings: a canonical hash that ignores the volatile
OPNsense <revision> metadata, and a per-path structural diff that reports WHICH element
paths changed (added/removed/modified) WITHOUT emitting their values (which may be secrets).
Element order is preserved (firewall rules are order-sensitive): repeated siblings are
indexed by position; siblings are never sorted.
"""

import hashlib
import xml.etree.ElementTree as ET  # Element type annotations only — NOT for parsing

from defusedxml.ElementTree import fromstring as _parse_xml  # XXE / billion-laughs safe

# Known-volatile top-level nodes that change on every save without a real config change.
_VOLATILE_TAGS = frozenset({"revision"})


def _strip_volatile(root: ET.Element) -> None:
    for child in list(root):
        if child.tag in _VOLATILE_TAGS:
            root.remove(child)


def _flatten(xml: str) -> dict[str, str]:
    """Map every leaf element / attribute to its value, keyed by an indexed path.

    Parses with defusedxml: hostile XML (XXE / billion-laughs) is refused (raises),
    never expanded. Callers treat a raise as "skip this config".
    """
    root = _parse_xml(xml)
    _strip_volatile(root)
    out: dict[str, str] = {}

    def walk(elem: ET.Element, path: str) -> None:
        for key, val in elem.attrib.items():
            out[f"{path}/@{key}"] = val
        children = list(elem)
        if not children:
            out[path] = (elem.text or "").strip()
            return
        tag_total: dict[str, int] = {}
        for child in children:
            tag_total[child.tag] = tag_total.get(child.tag, 0) + 1
        seen: dict[str, int] = {}
        for child in children:
            seen[child.tag] = seen.get(child.tag, 0) + 1
            seg = child.tag if tag_total[child.tag] == 1 else f"{child.tag}[{seen[child.tag]}]"
            walk(child, f"{path}/{seg}")

    walk(root, root.tag)
    return out


def canonical_hash(xml: str) -> str:
    """sha256 over the volatile-stripped flattened (path, value) pairs."""
    flat = _flatten(xml)
    blob = "\n".join(f"{p}={flat[p]}" for p in sorted(flat))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def structural_diff(xml_a: str, xml_b: str) -> list[dict]:
    """List of {path, change} where change in {added, removed, modified}. No values emitted."""
    a, b = _flatten(xml_a), _flatten(xml_b)
    changes: list[dict] = []
    for path in sorted(set(a) | set(b)):
        if path not in b:
            changes.append({"path": path, "change": "removed"})
        elif path not in a:
            changes.append({"path": path, "change": "added"})
        elif a[path] != b[path]:
            changes.append({"path": path, "change": "modified"})
    return changes

"""Schema-agnostic navigable model of a device config (read-only).

Parses config.xml with defusedxml (XXE/billion-laughs safe), strips the volatile
<revision> node, preserves element order (repeated siblings indexed by position, same
path scheme as config_diff), and emits a JSON tree. Sensitive leaf values (passwords,
keys, secrets...) are REDACTED: the node carries sensitive=True and value=None, and the
secret value never appears in the output. Redaction is conservative (when in doubt, redact).
"""

import xml.etree.ElementTree as ET  # type annotations only — NOT for parsing

from defusedxml.ElementTree import fromstring as _parse_xml

_VOLATILE_TAGS = frozenset({"revision"})

# Conservative denylist of tag substrings that indicate a secret-bearing field.
# A maintained security control: prefer over-redaction (a missed tag would leak a secret).
_SENSITIVE_SUBSTRINGS = (
    "password", "passwd", "secret", "psk", "pre-shared-key", "preshared",
    "passphrase", "privatekey", "private_key", "apikey", "api_key",
    "sharedkey", "shared_key", "token", "prv",
)


def is_sensitive(tag: str) -> bool:
    t = tag.lower()
    return any(sub in t for sub in _SENSITIVE_SUBSTRINGS)


def _strip_volatile(root: ET.Element) -> None:
    for child in list(root):
        if child.tag in _VOLATILE_TAGS:
            root.remove(child)


def _node(elem: ET.Element, path: str) -> dict:
    node: dict = {
        "tag": elem.tag,
        "path": path,
        "attributes": dict(elem.attrib),
        "children": [],
        "value": None,
        "sensitive": False,
    }
    children = list(elem)
    if not children:
        if is_sensitive(elem.tag):
            node["sensitive"] = True  # value stays None (redacted)
        else:
            node["value"] = (elem.text or "").strip()
        return node
    tag_total: dict[str, int] = {}
    for child in children:
        tag_total[child.tag] = tag_total.get(child.tag, 0) + 1
    seen: dict[str, int] = {}
    for child in children:
        seen[child.tag] = seen.get(child.tag, 0) + 1
        seg = child.tag if tag_total[child.tag] == 1 else f"{child.tag}[{seen[child.tag]}]"
        node["children"].append(_node(child, f"{path}/{seg}"))
    return node


def build_tree(xml: str) -> dict:
    root = _parse_xml(xml)
    _strip_volatile(root)
    return _node(root, root.tag)

"""Schema-agnostic navigable model of a device config (read-only).

Parses config.xml with defusedxml (XXE/billion-laughs safe), strips the volatile
<revision> node, preserves element order (repeated siblings indexed by position, same
path scheme as config_diff), and emits a JSON tree. Sensitive values (passwords, keys,
secrets...) are REDACTED: once a tag is sensitive the node AND its whole subtree carry
sensitive=True with value=None and nulled attribute values, so neither descendant text
nor attribute secrets ever appear in the output. Redaction is conservative (when in
doubt, redact).
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
    # OPNsense literal secret tags missed by the substrings above:
    # privkey (cert/WireGuard private keys), hash (password hashes),
    # seed (TOTP/OTP seeds). NB: "crypt" is intentionally excluded — it
    # over-matches <encryption> (non-secret settings); bcrypt/crypt hashes
    # are already covered by "hash" and "password".
    "privkey", "hash", "seed",
)


def is_sensitive(tag: str) -> bool:
    t = tag.lower()
    return any(sub in t for sub in _SENSITIVE_SUBSTRINGS)


def _strip_volatile(root: ET.Element) -> None:
    for child in list(root):
        if child.tag in _VOLATILE_TAGS:
            root.remove(child)


def _node(elem: ET.Element, path: str, redacted: bool = False) -> dict:
    # Once a node's tag is sensitive, the node AND its entire subtree are redacted:
    # the flag is propagated down so descendant text/attributes never leak.
    redacted = redacted or is_sensitive(elem.tag)
    # Redact attribute values that are themselves sensitively-keyed, or any
    # attribute under a redacted subtree.
    attributes = {
        k: (None if (redacted or is_sensitive(k)) else v) for k, v in elem.attrib.items()
    }
    node: dict = {
        "tag": elem.tag,
        "path": path,
        "attributes": attributes,
        "children": [],
        "value": None,
        "sensitive": redacted,
    }
    children = list(elem)
    if not children:
        if not redacted:
            node["value"] = (elem.text or "").strip()  # value stays None when redacted
        return node
    tag_total: dict[str, int] = {}
    for child in children:
        tag_total[child.tag] = tag_total.get(child.tag, 0) + 1
    seen: dict[str, int] = {}
    for child in children:
        seen[child.tag] = seen.get(child.tag, 0) + 1
        seg = child.tag if tag_total[child.tag] == 1 else f"{child.tag}[{seen[child.tag]}]"
        node["children"].append(_node(child, f"{path}/{seg}", redacted))
    return node


def build_tree(xml: str) -> dict:
    root = _parse_xml(xml)
    _strip_volatile(root)
    return _node(root, root.tag)

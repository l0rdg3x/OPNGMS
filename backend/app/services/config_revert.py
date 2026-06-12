"""Operator-triggered revert: build the inverse of a config_change and a snapshot reader.

The inverse is a normal config_change run through the existing apply pipeline; only the
inverse-generation is new. v1 registers the firewall_alias kind; other kinds raise NoInverseError
(the Revert button is disabled for them) until their builders are added.
"""
from __future__ import annotations

import gzip
from collections.abc import Callable

from defusedxml import ElementTree as DET

from app.core import crypto
from app.models.config_change import ConfigChange

# (operation, target, payload) for the inverse change.
InverseBuilder = Callable[[ConfigChange, str | None], tuple[str, str, dict]]


class NoInverseError(Exception):
    """No inverse can be built (unknown kind, or a delete/set with no pre-apply snapshot)."""


INVERSE_BUILDERS: dict[str, InverseBuilder] = {}


def register_inverse_builder(kind: str, fn: InverseBuilder) -> None:
    INVERSE_BUILDERS[kind] = fn


def has_inverse(kind: str) -> bool:
    return kind in INVERSE_BUILDERS


def build_inverse(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    fn = INVERSE_BUILDERS.get(change.kind)
    if fn is None:
        raise NoInverseError(f"no inverse builder for kind {change.kind!r}")
    return fn(change, snapshot_xml)


def snapshot_to_xml(content_enc: bytes) -> str:
    """Decrypt + gunzip a config_snapshot.content_enc back into the config.xml string."""
    return gzip.decompress(crypto.decrypt_bytes(bytes(content_enc))).decode("utf-8")


def alias_from_config_xml(xml: str, name: str) -> dict | None:
    """Extract the <alias> with the given <name> from a config.xml as a flat {tag: text} payload."""
    root = DET.fromstring(xml)
    for alias in root.iter("alias"):
        name_el = alias.find("name")
        if name_el is not None and (name_el.text or "") == name:
            return {child.tag: (child.text or "") for child in alias}
    return None


def _invert_alias(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    name = change.target or change.payload.get("name", "")
    if change.operation == "add":
        return "delete", name, {"name": name}
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the alias from")
    prev = alias_from_config_xml(snapshot_xml, name)
    if prev is None:
        raise NoInverseError(f"alias {name!r} not found in the pre-apply snapshot")
    prev.setdefault("name", name)
    inverse_op = "add" if change.operation == "delete" else "set"
    return inverse_op, name, prev


register_inverse_builder("alias", _invert_alias)

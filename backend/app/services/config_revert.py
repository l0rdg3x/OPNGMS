"""Operator-triggered revert: build the inverse of a config_change and a snapshot reader.

The inverse is a normal config_change run through the existing apply pipeline; only the
inverse-generation is new. v1 registers the firewall_alias kind; other kinds raise NoInverseError
(the Revert button is disabled for them) until their builders are added.
"""
from __future__ import annotations

import gzip
import uuid
from collections.abc import Callable, Iterable

from cryptography.fernet import InvalidToken
from defusedxml import ElementTree as DET
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.core import crypto
from app.models.config_change import ConfigChange
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.services.config_push import create_change

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


def setting_from_config_xml(xml: str, xml_path: str, dotted_keys: Iterable[str]) -> dict:
    """Read prior values for `dotted_keys` from config.xml under `xml_path`.

    A dotted key `general.enabled` resolves to the element `{xml_path}/general/enabled`.
    A missing element maps to "" (revert clears the field — the safest default)."""
    root = DET.fromstring(xml)
    out: dict = {}
    for key in dotted_keys:
        el = root.find(f"{xml_path}/{key.replace('.', '/')}")
        out[key] = (el.text or "") if el is not None else ""
    return out


def _invert_opnsense_setting(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    endpoint_key = change.target or (change.payload or {}).get("endpoint_key", "")
    ep = SETTING_ENDPOINTS.get(endpoint_key)
    if ep is None:
        raise NoInverseError(f"unknown setting endpoint {endpoint_key!r}")
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the setting from")
    changed = (change.payload or {}).get("payload", {})
    if not changed:
        raise NoInverseError("setting change has no fields to invert")
    prev = setting_from_config_xml(snapshot_xml, ep.xml_path, changed.keys())
    return "set", endpoint_key, {"endpoint_key": endpoint_key, "payload": prev}


register_inverse_builder("opnsense_setting", _invert_opnsense_setting)


def record_from_config_xml(xml: str, path: str, match: dict) -> dict | None:
    """Find the element under `path` whose child tags equal every (tag, value) in `match`;
    return its children as a flat {tag: text} dict, or None. (Generalizes alias_from_config_xml.)"""
    root = DET.fromstring(xml)
    for el in root.iterfind(f".//{path}"):
        if all((el.findtext(tag) or "") == val for tag, val in match.items()):
            return {child.tag: (child.text or "") for child in el}
    return None


_FW_RULE_PATH = "OPNsense/Firewall/Filter/rules/rule"


def _invert_firewall_rule(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    payload = change.payload or {}
    description = change.target or payload.get("description", "")
    interface = str(payload.get("interface", ""))
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the firewall rule from")
    prior = record_from_config_xml(snapshot_xml, _FW_RULE_PATH,
                                   {"description": description, "interface": interface})
    if prior is None:
        return "delete", description, {"description": description, "interface": interface}
    return "set", description, prior


register_inverse_builder("firewall_rule", _invert_firewall_rule)


_MONIT_TEST_PATH = "OPNsense/monit/test"


def _invert_monit_test(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    name = change.target or (change.payload or {}).get("name", "")
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the monit test from")
    prior = record_from_config_xml(snapshot_xml, _MONIT_TEST_PATH, {"name": name})
    if prior is None:
        return "delete", name, {"name": name}
    return "set", name, prior


register_inverse_builder("monit_test", _invert_monit_test)


# --- revert flow ---


class RevertError(Exception):
    """The change cannot be reverted (wrong state, or no inverse for its kind)."""


REVERTIBLE_STATES = ("applied", "failed")


async def revert_change(session: AsyncSession, change: ConfigChange, *, actor_id: uuid.UUID) -> ConfigChange:
    """Build the inverse of `change` as a new draft config_change linked via reverts_change_id.

    The caller schedules/applies the returned draft through the normal pipeline.
    """
    if change.status not in REVERTIBLE_STATES:
        raise RevertError(f"cannot revert a change in status {change.status!r}")
    if not has_inverse(change.kind):
        raise RevertError(f"revert not supported for kind {change.kind!r}")
    snapshot_xml: str | None = None
    if change.pre_apply_snapshot_id is not None:
        snap = await ConfigSnapshotRepository(session, change.tenant_id).get(change.pre_apply_snapshot_id)
        if snap is not None:
            try:
                snapshot_xml = snapshot_to_xml(snap.content_enc)
            except (InvalidToken, gzip.BadGzipFile, EOFError, UnicodeDecodeError) as exc:
                # A snapshot that can't be decrypted (e.g. MASTER_KEY rotated past its retention)
                # is a 409, not an unhandled 500 that leaks a stack trace.
                raise RevertError("pre-apply snapshot could not be decrypted") from exc
    op, target, payload = build_inverse(change, snapshot_xml)  # may raise NoInverseError
    inverse = await create_change(
        session,
        tenant_id=change.tenant_id,
        device_id=change.device_id,
        created_by=actor_id,
        kind=change.kind,
        operation=op,
        target=target,
        payload=payload,
    )
    inverse.reverts_change_id = change.id
    await session.flush()
    return inverse

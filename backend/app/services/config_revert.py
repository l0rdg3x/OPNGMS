"""Operator-triggered revert: build the inverse of a config_change and a snapshot reader.

The inverse is a normal config_change run through the existing apply pipeline; only the
inverse-generation is new. v1 registers the firewall_alias kind; other kinds raise NoInverseError
(the Revert button is disabled for them) until their builders are added.
"""
from __future__ import annotations

import gzip
import json
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
    return its children as a flat {tag: text} dict, or None. (Generalizes alias_from_config_xml.)

    `path` must be a trusted module-level constant — it is embedded in the ElementTree query, not
    sanitized; never pass a value derived from a change payload or other untrusted input."""
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
    if change.operation == "add":
        # The inverse of a creation is a delete by identity — no snapshot needed (mirrors _invert_alias).
        return "delete", description, {"description": description, "interface": interface}
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
    if change.operation == "add":
        # The inverse of a creation is a delete by identity — no snapshot needed (mirrors _invert_alias).
        return "delete", name, {"name": name}
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the monit test from")
    prior = record_from_config_xml(snapshot_xml, _MONIT_TEST_PATH, {"name": name})
    if prior is None:
        return "delete", name, {"name": name}
    return "set", name, prior


register_inverse_builder("monit_test", _invert_monit_test)


_IDS_POLICY_PATH = "OPNsense/IDS/policies/policy"
_IDS_FILES_PATH = "OPNsense/IDS/files/file"


def _ids_files_map(xml: str) -> dict:
    """Build {file_uuid: filename} from the IDS files table in the snapshot.

    The policy stores its rulesets as file-uuids; the portable body refers to them by filename, so
    the inverse maps each uuid back through this table (the caller fails closed on an unresolved uuid)."""
    root = DET.fromstring(xml)
    out: dict = {}
    for f in root.iterfind(f".//{_IDS_FILES_PATH}"):
        file_uuid = f.get("uuid")
        name = f.findtext("filename")
        if file_uuid and name:
            out[file_uuid] = name
    return out


def _invert_ids_policy(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    description = change.target or (change.payload or {}).get("description", "")
    if change.operation == "add":
        # The inverse of a creation is a delete by identity — no snapshot needed (mirrors _invert_alias).
        return "delete", description, {"description": description}
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the ids policy from")
    prior = record_from_config_xml(snapshot_xml, _IDS_POLICY_PATH, {"description": description})
    if prior is None:
        return "delete", description, {"description": description}
    files = _ids_files_map(snapshot_xml)
    rulesets = []
    for u in (prior.get("rulesets", "") or "").split(","):
        if not u:
            continue
        name = files.get(u)
        if name is None:
            # Fail closed: a valid snapshot lists every policy-referenced file in its own files table,
            # and the connector refuses an unresolvable ruleset at apply time — don't silently rebuild a
            # policy with fewer rulesets than the original.
            raise NoInverseError(f"ids policy ruleset file {u!r} is not in the snapshot files table")
        rulesets.append(name)
    raw_content = prior.get("content") or ""
    try:
        content = json.loads(raw_content) if raw_content else {}
    except json.JSONDecodeError as exc:
        raise NoInverseError("ids policy snapshot content is not valid JSON") from exc
    body = {
        "description": description,
        "enabled": prior.get("enabled", "1"),
        "prio": prior.get("prio", "0"),
        "new_action": prior.get("new_action", "alert"),
        "action": [a for a in (prior.get("action", "") or "").split(",") if a],
        "rulesets": rulesets,
        "content": content,
    }
    return "set", description, body


register_inverse_builder("ids_policy", _invert_ids_policy)


def _grid_row_by_uuid(xml: str, xml_path: str, row_uuid: str) -> dict | None:
    """Find the element with `uuid == row_uuid` in the snapshot subtree under `xml_path`;
    return its child tags as a flat {tag: text} dict, or None if absent.

    `xml_path` must be a trusted value carried in the change payload (set server-side from the
    catalog model), never raw client input — it is embedded in the ElementTree query, not sanitized.
    The uuid is compared by equality only, so it is never embedded in the query path."""
    root = DET.fromstring(xml)
    subtree = root.find(xml_path)
    if subtree is None:
        return None
    for el in subtree.iter():
        if el.get("uuid") == row_uuid:
            return {child.tag: (child.text or "") for child in el}
    return None


def _invert_catalog_setting(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    """Invert a generic catalog_setting change into a single catalog_setting `set`.

    Scalars restore the snapshot's prior values; each forward grid op is inverted (del<->add by uuid,
    set restores the prior row). The forward ops are walked in payload order and each is replaced by
    its inverse. Every grid op targets a distinct uuid (forward set/del require distinct existing
    uuids; an add creates a fresh row), so the inverses are mutually independent and the applied
    order does not affect the restored state. add->del needs only `change.result` (the box-assigned
    uuid), so a pure-add change inverts with no snapshot; any scalar or del/set op needs the snapshot
    (and the model's xml_path) to reconstruct prior state, so their absence is a NoInverseError."""
    payload = change.payload or {}
    model_id = change.target or payload.get("model_id", "")
    xml_path = payload.get("xml_path", "")
    scalars = payload.get("scalars") or {}
    fwd_grids = payload.get("grids") or []
    result_grids = ((change.result or {}).get("grids")) or []

    needs_snapshot = bool(scalars) or any(g.get("op") in ("del", "set") for g in fwd_grids)
    if needs_snapshot and not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the catalog setting from")
    if needs_snapshot and not xml_path:
        # An older catalog change with no recorded xml_path can't be located in the snapshot; an empty
        # path would make ElementTree read from the document root (wrong subtree) — fail closed instead.
        raise NoInverseError("catalog change has no xml_path — cannot reconstruct prior state")

    inv_scalars = (
        setting_from_config_xml(snapshot_xml, xml_path, scalars.keys())
        if scalars and snapshot_xml else {}
    )

    inv_grids: list[dict] = []
    # Walk forward ops in payload order, replacing each by its inverse. The applier appends result
    # entries in payload order, so result_grids is index-aligned with fwd_grids. (The early guard
    # above guarantees snapshot_xml is present whenever a del/set op is reached.)
    for i, fwd in enumerate(fwd_grids):
        op = fwd.get("op")
        base = {"endpoints": fwd.get("endpoints", {}), "row": fwd.get("row", "")}
        if op == "del":
            prior = _grid_row_by_uuid(snapshot_xml, xml_path, fwd.get("uuid"))
            if prior is None:
                raise NoInverseError(
                    f"deleted grid row {fwd.get('uuid')!r} not found in the pre-apply snapshot")
            inv_grids.append({"op": "add", **base, "uuid": None, "item": prior})
        elif op == "set":
            prior = _grid_row_by_uuid(snapshot_xml, xml_path, fwd.get("uuid"))
            if prior is None:
                raise NoInverseError(
                    f"modified grid row {fwd.get('uuid')!r} not found in the pre-apply snapshot")
            inv_grids.append({"op": "set", **base, "uuid": fwd.get("uuid"), "item": prior})
        elif op == "add":
            # The inverse of an add is a del of the NEWLY assigned uuid, read from the live result.
            # If the op never really applied (dry-run / missing / no uuid), nothing was added -> skip.
            res = result_grids[i] if i < len(result_grids) else None
            new_uuid = ((res or {}).get("result") or {}).get("uuid") if res and not res.get("dry_run") else None
            if new_uuid:
                inv_grids.append({"op": "del", **base, "uuid": new_uuid, "item": None})

    inverse_payload = {
        "model_id": model_id,
        "set_path": payload.get("set_path", ""),
        "reconfigure_path": payload.get("reconfigure_path", ""),
        "model_root": payload.get("model_root", ""),
        "xml_path": xml_path,
        "scalars": inv_scalars,
        "grids": inv_grids,
    }
    return "set", model_id, inverse_payload


register_inverse_builder("catalog_setting", _invert_catalog_setting)


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

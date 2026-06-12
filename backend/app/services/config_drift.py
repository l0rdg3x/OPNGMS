"""On-demand drift detection: compare a device's LIVE config against what we APPLIED.

For each applied, template-sourced config_change (latest per (kind, target)), a pure per-kind
checker compares the fields we set to the live device state and reports the drifted field names
(names only — never raw values, so no config secret leaks into the response).

Coverage is a registry: `opnsense_setting`, `alias` and `ids_rulesets` ship reliable checkers;
`firewall_rule`/`monit_test` are intentionally NOT registered (they surface as "unsupported", never
falsely "in sync") until their config.xml<->API field map is hardware-verified — the same blocker
as their inverse builders.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.models.config_change import ConfigChange
from app.services.config_revert import alias_from_config_xml, setting_from_config_xml

# Drift statuses.
IN_SYNC = "in_sync"
DRIFTED = "drifted"
MISSING = "missing"          # the applied resource is no longer present on the device
UNSUPPORTED = "unsupported"  # no checker for this kind yet


@dataclass(frozen=True)
class LiveState:
    """One live snapshot of the device used by all checkers (gathered once per probe)."""
    config_xml: str
    ruleset_enabled: dict[str, bool]   # IDS ruleset filename -> enabled (empty if not fetched)


@dataclass(frozen=True)
class DriftResult:
    change_id: uuid.UUID
    kind: str
    target: str
    status: str
    drifted_fields: list[str]


# A checker is pure: (change, live) -> (status, drifted_field_names).
DriftChecker = Callable[[ConfigChange, LiveState], tuple[str, list[str]]]
DRIFT_CHECKERS: dict[str, DriftChecker] = {}


def register_drift_checker(kind: str, fn: DriftChecker) -> None:
    DRIFT_CHECKERS[kind] = fn


def has_drift_checker(kind: str) -> bool:
    return kind in DRIFT_CHECKERS


def _norm(value: object) -> str:
    """Render an applied payload value into the string form stored in config.xml."""
    if isinstance(value, (list, tuple)):
        return "\n".join(str(v) for v in value)
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _check_opnsense_setting(change: ConfigChange, live: LiveState) -> tuple[str, list[str]]:
    endpoint_key = change.target or (change.payload or {}).get("endpoint_key", "")
    ep = SETTING_ENDPOINTS.get(endpoint_key)
    if ep is None:
        return UNSUPPORTED, []
    changed: dict = (change.payload or {}).get("payload", {})
    prev = setting_from_config_xml(live.config_xml, ep.xml_path, changed.keys())
    drifted = [k for k, v in changed.items() if _norm(v) != prev.get(k, "")]
    return (DRIFTED if drifted else IN_SYNC), drifted


def _check_alias(change: ConfigChange, live: LiveState) -> tuple[str, list[str]]:
    name = change.target or (change.payload or {}).get("name", "")
    cur = alias_from_config_xml(live.config_xml, name)
    if cur is None:
        return MISSING, []
    applied: dict = change.payload or {}
    drifted = [k for k, v in applied.items() if k != "name" and _norm(v) != cur.get(k, "")]
    return (DRIFTED if drifted else IN_SYNC), drifted


def _check_ids_rulesets(change: ConfigChange, live: LiveState) -> tuple[str, list[str]]:
    rulesets = (change.payload or {}).get("rulesets", [])
    drifted = [f for f in rulesets if not live.ruleset_enabled.get(f, False)]
    return (DRIFTED if drifted else IN_SYNC), drifted


register_drift_checker("opnsense_setting", _check_opnsense_setting)
register_drift_checker("alias", _check_alias)
register_drift_checker("ids_rulesets", _check_ids_rulesets)


def _selectable(change: ConfigChange) -> bool:
    """Only applied, template-sourced changes describe a template-governed live value."""
    return change.status == "applied" and change.source_template_id is not None


def _latest_per_target(changes: Sequence[ConfigChange]) -> list[ConfigChange]:
    """Keep the newest selectable change per (kind, target). `changes` is created_at desc."""
    seen: set[tuple[str, str]] = set()
    out: list[ConfigChange] = []
    for c in changes:
        if not _selectable(c):
            continue
        key = (c.kind, c.target or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def needs_rulesets(changes: Iterable[ConfigChange]) -> bool:
    """True if any selectable change is an IDS-ruleset change (so the orchestrator fetches them)."""
    return any(_selectable(c) and c.kind == "ids_rulesets" for c in changes)


def compute_drift(changes: Sequence[ConfigChange], live: LiveState) -> list[DriftResult]:
    """Pure: dedupe to the latest applied template change per target, run each through its checker."""
    results: list[DriftResult] = []
    for change in _latest_per_target(changes):
        checker = DRIFT_CHECKERS.get(change.kind)
        if checker is None:
            status, fields = UNSUPPORTED, []
        else:
            status, fields = checker(change, live)
        results.append(DriftResult(
            change_id=change.id, kind=change.kind, target=change.target or "",
            status=status, drifted_fields=fields))
    return results


def unsupported_kinds(changes: Sequence[ConfigChange]) -> list[str]:
    """Kinds present among the selectable changes that have no checker yet (sorted, deduped)."""
    return sorted({c.kind for c in _latest_per_target(changes) if not has_drift_checker(c.kind)})


async def gather_live_state(client, changes: Sequence[ConfigChange]) -> LiveState:
    """Fetch the device's live config.xml once (+ the IDS ruleset catalog only if needed)."""
    config_xml = await client.get_config_backup()
    ruleset_enabled: dict[str, bool] = {}
    if needs_rulesets(changes):
        for row in await client.list_ids_rulesets():
            name = row.get("filename")
            if name:
                ruleset_enabled[name] = str(row.get("enabled", "")).lower() in ("1", "true", "yes")
    return LiveState(config_xml=config_xml, ruleset_enabled=ruleset_enabled)

"""Register the curated `ids_policy` template kind + its config-change applier.

A template body is a portable Suricata/IDS policy (rule-action tuning): identity = `description`;
the connector upserts by description. `rulesets` are referenced by filename and resolved to the
device's enabled ruleset-file uuids at apply time."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"disable", "alert", "drop"}                       # current-action FILTER: rules a policy matches
_NEW_ACTIONS = {"default", "alert", "drop", "disable"}        # OUTCOME: what the matched rules become
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")         # mirror the connector's charset guard
_CONTENT_KEY_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")          # metadata-filter keys (defence in depth)
_INT_RE = re.compile(r"\A-?\d+\Z")


def _validate(body: dict) -> None:
    body = body or {}
    description = str(body.get("description", ""))
    if not description.strip():
        raise InvalidTemplateError("ids policy 'description' is required (it is the policy identity)")
    if description != description.strip():
        raise InvalidTemplateError("ids policy 'description' must not have leading/trailing whitespace")
    if str(body.get("enabled", "1")) not in ("0", "1"):
        raise InvalidTemplateError("ids policy 'enabled' must be '0' or '1'")
    if not _INT_RE.match(str(body.get("prio", "0"))):
        raise InvalidTemplateError("ids policy 'prio' must be an integer")
    actions = body.get("action", [])
    if not isinstance(actions, list) or any(a not in _ACTIONS for a in actions):
        raise InvalidTemplateError(f"ids policy 'action' must be a list of {sorted(_ACTIONS)}")
    if body.get("new_action", "alert") not in _NEW_ACTIONS:
        raise InvalidTemplateError(f"ids policy 'new_action' must be one of {sorted(_NEW_ACTIONS)}")
    rulesets = body.get("rulesets", [])
    if not isinstance(rulesets, list) or any(
        not isinstance(n, str) or not _RULESET_NAME_RE.match(n) for n in rulesets
    ):
        raise InvalidTemplateError("ids policy 'rulesets' must be a list of ruleset filenames")
    content = body.get("content", {})
    if not isinstance(content, dict) or any(
        not isinstance(k, str) or not _CONTENT_KEY_RE.match(k)
        or not isinstance(v, list) or any(not isinstance(x, str) for x in v)
        for k, v in content.items()
    ):
        raise InvalidTemplateError("ids policy 'content' must be an object of metadata-key -> [values]")


register_template_kind("ids_policy", TemplateKind(
    validate=_validate,
    change_kind="ids_policy",
    to_change=lambda body: ("set", str(body.get("description", "")), body),
    pinned=("description",),
))


async def _apply_ids_policy(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_ids_policy(operation, payload, dry_run=dry_run)


register_change_applier("ids_policy", _apply_ids_policy)

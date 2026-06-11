"""Register the curated `suricata_ruleset` template kind + its config-change applier.

A template body is `{"rulesets": [filename, ...]}` — the IDS rulesets to ENABLE. Apply is
additive/non-destructive (it enables the listed rulesets; it never disables others)."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

# Mirrors the connector's URL-path charset guard (anti path-injection); validated server-side too.
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def _validate(body: dict) -> None:
    body = body or {}
    rulesets = body.get("rulesets")
    if not isinstance(rulesets, list) or not rulesets:
        raise InvalidTemplateError("'rulesets' must be a non-empty list")
    for name in rulesets:
        if not isinstance(name, str) or not _RULESET_NAME_RE.match(name):
            raise InvalidTemplateError(f"invalid ruleset filename: {name!r}")


register_template_kind("suricata_ruleset", TemplateKind(
    validate=_validate,
    change_kind="ids_rulesets",
    to_change=lambda body: ("set", "ids_rulesets", body),
    pinned=(),                                   # no identity field; override replaces the whole list
))


async def _apply_ids_rulesets(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_ids_rulesets(operation, payload, dry_run=dry_run)


register_change_applier("ids_rulesets", _apply_ids_rulesets)

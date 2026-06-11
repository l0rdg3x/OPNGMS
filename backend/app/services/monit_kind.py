"""Register the curated `monit_test` template kind + its config-change applier.

Body = a portable Monit health-check test {name, type, condition, action, path}. Identity = `name`;
the connector upserts by name. A test takes effect once attached to a Monit service."""
from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"alert", "restart", "start", "stop", "exec", "unmonitor"}


def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("name", "")).strip():
        raise InvalidTemplateError("monit test 'name' is required (it is the test identity)")
    if not str(body.get("type", "")).strip():
        raise InvalidTemplateError("monit test 'type' is required")
    if not str(body.get("condition", "")).strip():
        raise InvalidTemplateError("monit test 'condition' is required")
    if body.get("action") not in _ACTIONS:
        raise InvalidTemplateError(f"monit test 'action' must be one of {sorted(_ACTIONS)}")
    av = body.get("attach_to_system")
    if av is not None and str(av) not in ("0", "1"):
        raise InvalidTemplateError("monit test 'attach_to_system' must be '0' or '1'")


register_template_kind("monit_test", TemplateKind(
    validate=_validate,
    change_kind="monit_test",
    to_change=lambda body: ("set", str(body.get("name", "")), body),
    pinned=("name",),
))


async def _apply_monit_test(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_monit_test(operation, payload, dry_run=dry_run)


register_change_applier("monit_test", _apply_monit_test)

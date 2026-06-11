"""Register the generic `opnsense_setting` template kind + its config-change applier."""
from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind


def _validate(body: dict) -> None:
    body = body or {}
    ep = SETTING_ENDPOINTS.get(body.get("endpoint_key"))
    if ep is None:
        raise InvalidTemplateError(f"unknown setting endpoint: {body.get('endpoint_key')!r}")
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise InvalidTemplateError("setting 'payload' must be an object")
    bad = set(payload) & set(ep.exclude_fields)
    if bad:
        raise InvalidTemplateError(f"hardware/device-specific fields are not templatable: {sorted(bad)}")


register_template_kind("opnsense_setting", TemplateKind(
    validate=_validate,
    change_kind="opnsense_setting",
    to_change=lambda body: ("set", body["endpoint_key"], body),   # payload = the whole body
    pinned=("endpoint_key",),                                      # override may tweak payload, not repoint
))


async def _apply_opnsense_setting(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    ep = SETTING_ENDPOINTS.get(payload.get("endpoint_key"))
    if ep is None:
        raise InvalidTemplateError(f"unknown setting endpoint: {payload.get('endpoint_key')!r}")
    return await client.apply_setting(
        ep.set_path, ep.reconfigure_path, ep.model_root, payload.get("payload", {}), dry_run=dry_run)


register_change_applier("opnsense_setting", _apply_opnsense_setting)

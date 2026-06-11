"""Configuration-template engine (M1).

Validates the typed firmware/firewall body for a kind, computes the effective body
(base template merged with a per-tenant override patch), and materializes a config_change
that the existing config-push pipeline applies. M1 supports the `firewall_alias` kind only."""
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.services.config_push import create_change

ALIAS_TYPES = {"host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"}
_PINNED = ("name", "type")  # identity fields an override may not change


class InvalidTemplateError(ValueError):
    """A template/effective body failed validation."""


def validate_alias_body(body: dict) -> None:
    body = body or {}
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidTemplateError("alias 'name' is required")
    if body.get("type") not in ALIAS_TYPES:
        raise InvalidTemplateError(f"alias 'type' must be one of {sorted(ALIAS_TYPES)}")
    content = body.get("content")
    if not isinstance(content, list) or not content:
        raise InvalidTemplateError("alias 'content' must be a non-empty list")


_VALIDATORS = {"firewall_alias": validate_alias_body}


def validate_body(kind: str, body: dict) -> None:
    validator = _VALIDATORS.get(kind)
    if validator is None:
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    validator(body)


def effective_body(kind: str, base: dict, patch: dict | None) -> dict:
    """Shallow per-key merge of base with the override patch; identity fields stay pinned to base."""
    merged = {**(base or {}), **(patch or {})}
    for key in _PINNED:
        if key in (base or {}):
            merged[key] = base[key]
    return merged


async def materialize_change(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID, created_by: uuid.UUID,
    template_id: uuid.UUID, kind: str, body: dict,
) -> ConfigChange:
    """Turn an effective `firewall_alias` body into a draft config_change (kind='alias', op='set')."""
    validate_body(kind, body)
    if kind != "firewall_alias":  # M1 maps only firewall_alias -> the config-push 'alias' kind
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind="alias", operation="set", target=body["name"], payload=body,
    )
    change.source_template_id = template_id
    await session.flush()
    return change

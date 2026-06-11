"""Configuration-template engine.

Validates the typed firmware/firewall body for a kind, computes the effective body
(base template merged with a per-tenant override patch), and materializes a config_change
that the existing config-push pipeline applies. Template kinds register themselves via
`register_template_kind`; M1's `firewall_alias` kind is pre-seeded."""
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.services.config_push import create_change

ALIAS_TYPES = {"host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"}


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


@dataclass(frozen=True)
class TemplateKind:
    """How a template kind validates, maps to a config_change, and pins identity fields."""

    validate: Callable[[dict], None]
    change_kind: str                                    # the config_change.kind it materializes to
    to_change: Callable[[dict], tuple[str, str, dict]]  # body -> (operation, target, payload)
    pinned: tuple[str, ...]                             # body keys an override may not change


TEMPLATE_KINDS: dict[str, TemplateKind] = {}


def register_template_kind(kind: str, spec: TemplateKind) -> None:
    TEMPLATE_KINDS[kind] = spec


# --- firewall_alias (M1) ---
register_template_kind("firewall_alias", TemplateKind(
    validate=validate_alias_body,
    change_kind="alias",
    to_change=lambda body: ("set", body["name"], body),
    pinned=("name", "type"),
))


def _kind(kind: str) -> TemplateKind:
    spec = TEMPLATE_KINDS.get(kind)
    if spec is None:
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    return spec


def validate_body(kind: str, body: dict) -> None:
    _kind(kind).validate(body or {})


def effective_body(kind: str, base: dict, patch: dict | None) -> dict:
    """Shallow per-key merge; the kind's identity (`pinned`) fields stay pinned to base."""
    spec = _kind(kind)
    merged = {**(base or {}), **(patch or {})}
    for key in spec.pinned:
        if key in (base or {}):
            merged[key] = base[key]
    return merged


async def materialize_change(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID, created_by: uuid.UUID,
    template_id: uuid.UUID, kind: str, body: dict,
) -> ConfigChange:
    """Validate the effective body and materialize a draft config_change for the kind."""
    spec = _kind(kind)
    spec.validate(body or {})
    operation, target, payload = spec.to_change(body)
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind=spec.change_kind, operation=operation, target=target, payload=payload,
    )
    change.source_template_id = template_id
    await session.flush()
    return change

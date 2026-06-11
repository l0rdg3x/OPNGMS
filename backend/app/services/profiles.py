"""Apply a profile = fan out to one config_change per member template, in order.

Reuses the M1 template engine (effective body + materialize) and the config-push pipeline.
Validation is atomic (a single invalid member fails the whole apply before anything is created);
the device-level apply of the produced changes is NOT atomic (each runs independently)."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.models.config_profile import ConfigProfile, ConfigProfileMember
from app.models.config_template import ConfigTemplate
from app.models.template_override import TemplateOverride
from app.services.templates import (
    InvalidTemplateError,
    effective_body,
    materialize_change,
    validate_body,
)


async def _ordered_members(session: AsyncSession, profile_id: uuid.UUID) -> list[ConfigTemplate]:
    rows = (await session.execute(
        select(ConfigTemplate)
        .join(ConfigProfileMember, ConfigProfileMember.template_id == ConfigTemplate.id)
        .where(ConfigProfileMember.profile_id == profile_id)
        .order_by(ConfigProfileMember.position, ConfigProfileMember.id)
    )).scalars().all()
    return list(rows)


async def _effective(session: AsyncSession, tenant_id: uuid.UUID, tpl: ConfigTemplate) -> dict:
    ov = (await session.execute(
        select(TemplateOverride).where(
            TemplateOverride.template_id == tpl.id, TemplateOverride.tenant_id == tenant_id)
    )).scalar_one_or_none()
    return effective_body(tpl.kind, tpl.body, ov.body_patch if ov else {})


async def materialize_profile(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
    created_by: uuid.UUID, profile: ConfigProfile, bindings: dict | None = None,
) -> list[ConfigChange]:
    """Validate ALL member effective bodies, then materialize one config_change per member (in order).

    `bindings` are apply-time inputs (e.g. {"interface": "wan"}) threaded into each member; the bind
    hook is per-kind (only firewall_rule consumes `interface`), so this is a no-op for other kinds."""
    templates = await _ordered_members(session, profile.id)
    if not templates:
        raise InvalidTemplateError("profile has no member templates")
    # 1) validate everything first (atomic validation; nothing created on a bad member)
    effective = []
    for tpl in templates:
        body = await _effective(session, tenant_id, tpl)
        validate_body(tpl.kind, body)
        effective.append((tpl, body))
    # 2) materialize one change per member, tag with the profile
    changes: list[ConfigChange] = []
    for tpl, body in effective:
        change = await materialize_change(
            session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
            template_id=tpl.id, kind=tpl.kind, body=body, bindings=bindings,
        )
        change.source_profile_id = profile.id
        changes.append(change)
    await session.flush()
    return changes

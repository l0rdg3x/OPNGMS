from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session, reset_tenant_context, set_tenant_context
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.tenant import Tenant
from app.models.user import User
from app.repositories.tenant_retention import TenantRetentionRepository
from app.schemas.system import (
    LivePushIn,
    LivePushOut,
    RetentionImpact,
    RuntimeSettingOut,
    RuntimeSettingsOut,
    RuntimeSettingsPatch,
)
from app.services.app_settings import get_live_push, set_live_push
from app.services.audit import AuditService
from app.services.report_retention import schedule_retention_warnings
from app.services.retention import RETENTION_STORES
from app.services.runtime_settings import (
    active_settings,
    get_runtime_config,
    runtime_defaults,
    update_runtime_config,
)

router = APIRouter(prefix="/api/admin", tags=["system"])


async def _runtime_settings_out(
    session: AsyncSession, impacts: list[RetentionImpact] | None = None
) -> RuntimeSettingsOut:
    # Reads ``app_settings`` (NOT an RLS-scoped table), so a leftover tenant GUC from the impacted-tenants
    # loop does not affect this build.
    effective = await get_runtime_config(session)
    defaults = runtime_defaults()
    return RuntimeSettingsOut(
        settings=[
            RuntimeSettingOut(
                key=r.key,
                value=effective[r.key],
                default=defaults[r.key],
                kind=r.kind.__name__,
                minimum=r.minimum,
                maximum=r.maximum,
                group=r.group,
            )
            for r in active_settings()
        ],
        retention_impacts=impacts or [],
    )


async def _retention_impacts(
    session: AsyncSession, lowered: list[str]
) -> list[RetentionImpact]:
    """Tenants bitten by a just-lowered GLOBAL retention default (SP-1 PR4c — superadmin feedback).

    For each lowered store, a tenant is impacted when it (a) has NO per-tenant override for that store (so it
    follows the global) AND (b) has an enabled, fixed-window schedule whose covered range now exceeds the new
    global. ``tenants`` is NOT an RLS-scoped table, so ``opngms_app`` may enumerate it; each tenant's data is
    then read UNDER RLS by setting that tenant's context and reusing the PR4b ``schedule_retention_warnings``
    helper — one tenant at a time, never an owner connection.
    """
    rows = (await session.execute(select(Tenant.id, Tenant.name))).all()
    impacts: list[RetentionImpact] = []
    try:
        for tenant_id, tenant_name in rows:
            await set_tenant_context(session, tenant_id)
            warnings = await schedule_retention_warnings(session, tenant_id)
            if not warnings:
                continue
            overrides = await TenantRetentionRepository(session, tenant_id).get_overrides()
            for w in warnings:
                store = w["limiting_store"]
                # Only the GLOBAL change bit this tenant: the limiting store was lowered AND the tenant has
                # no override for it (an overridden store uses the tenant's own value, not the global).
                if store in lowered and store not in overrides:
                    impacts.append(
                        RetentionImpact(
                            tenant_id=tenant_id,
                            tenant_name=tenant_name,
                            store=store,
                            range_days=w["range_days"],
                            bound=w["bound"],
                        )
                    )
    finally:
        # Drop the leftover tenant GUC back to the fail-closed neutral state. The final response build reads
        # only ``app_settings`` (not RLS-scoped), but reset defensively so any later query on this session
        # can't accidentally inherit the last tenant's context.
        await reset_tenant_context(session)
    return impacts


@router.get("/settings", response_model=RuntimeSettingsOut)
async def get_runtime_settings(
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RuntimeSettingsOut:
    return await _runtime_settings_out(session)


@router.put("/settings", response_model=RuntimeSettingsOut, dependencies=[Depends(enforce_csrf)])
async def update_runtime_settings(
    body: RuntimeSettingsPatch,
    request: Request,
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> RuntimeSettingsOut:
    # Only expose the active settings for editing; an inactive key is treated as unknown (its consumer
    # is not wired yet, so accepting it would be a silent no-op).
    allowed = {r.key for r in active_settings()}
    unknown = sorted(k for k in body.values if k not in allowed)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unknown setting(s): {', '.join(unknown)}",
        )
    before = await get_runtime_config(session)
    try:
        await update_runtime_config(session, body.values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    after = await get_runtime_config(session)
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="system.runtime_config",
        ip=request.client.host if request.client else None,
        details={"keys": sorted(body.values)},
    )
    # NB the audit write above intentionally precedes the tenant-context scan below: it runs under the
    # neutral GUC (tenant_id=None for this org action). Keep it before `_retention_impacts`, which mutates
    # the session's tenant context per tenant.
    # Only a LOWERED global retention default can newly bite a tenant that follows the global; compute the
    # impacted-tenants feedback before committing (read-only scan, no writes of its own). Use `.get()` so a
    # future RETENTION_STORES entry without a matching runtime key (e.g. SP-2's log_lake) can't KeyError the
    # whole PUT — it's just skipped until its key is wired.
    lowered = [
        store
        for store in RETENTION_STORES
        if (a := after.get(f"{store}_retention_days")) is not None
        and (b := before.get(f"{store}_retention_days")) is not None
        and int(a) < int(b)
    ]
    impacts = await _retention_impacts(session, lowered) if lowered else []
    await session.commit()
    return await _runtime_settings_out(session, impacts)


@router.get("/live-push", response_model=LivePushOut)
async def get_live_push_setting(
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> LivePushOut:
    return LivePushOut(enabled=await get_live_push(session, env_default=get_settings().live_push_enabled))


@router.put("/live-push", response_model=LivePushOut, dependencies=[Depends(enforce_csrf)])
async def set_live_push_setting(
    body: LivePushIn,
    request: Request,
    user: User = Depends(require_org(Action.SYSTEM_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> LivePushOut:
    await set_live_push(session, body.enabled)
    await AuditService(session).record(
        actor_user_id=user.id,
        tenant_id=None,
        action="system.live_push",
        ip=request.client.host if request.client else None,
        details={"enabled": body.enabled},
    )
    await session.commit()
    return LivePushOut(enabled=body.enabled)

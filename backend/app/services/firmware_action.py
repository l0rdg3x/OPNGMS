"""Run a scheduled/now firmware action against a device: update, upgrade, plugin install/remove.

Reboot-tolerant: the device going unreachable during a reboot is expected; only exceeding the
poll budget marks the action failed. A major upgrade runs as a multi-step loop (update/upgrade
then reboot, repeated until the device reports up to date). Plugin install is refused unless the
firmware is up to date (OPNsense pins the plugin repo to the running firmware)."""
import asyncio
import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense import parsers
from app.connectors.opnsense.client import OpnsenseError, ReachabilityError
from app.models.firmware_action import FirmwareAction
from app.services.config_push import _advisory_key

logger = logging.getLogger(__name__)

MAX_UPGRADE_STEPS = 6
MAX_STATUS_POLLS = 360       # ~30 min at POLL_INTERVAL
REBOOT_MAX_POLLS = 180       # ~15 min waiting for the box to come back
POLL_INTERVAL = 5.0
STARTUP_GRACE_POLLS = 6      # polls to wait for an action to enter "running" before treating "done" as no-op


def to_int(v) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return 0


def updates_pending(status: dict) -> bool:
    """firmware/status: status 'ok' means upgrades available; a positive `updates` count too."""
    status = status or {}
    return str(status.get("status", "")).lower() == "ok" or to_int(status.get("updates")) > 0


def major_offered(status: dict) -> bool:
    """A newer MAJOR (different series) is offered."""
    status = status or {}
    cur = status.get("product_version") or (status.get("product") or {}).get("product_version", "")
    latest = status.get("product_latest") or (status.get("product") or {}).get("product_latest", "")
    return bool(latest) and parsers.parse_version(latest) > parsers.parse_version(cur) \
        and parsers.series_of(latest) != parsers.series_of(cur)


async def _wait_until_reachable(client) -> None:
    for _ in range(REBOOT_MAX_POLLS):
        try:
            await client.test_connection()
            return
        except ReachabilityError:
            await asyncio.sleep(POLL_INTERVAL)
    raise OpnsenseError("device did not come back after reboot within budget")


async def poll_until_done(client) -> dict:
    """Poll upgradestatus until the running op finishes; tolerate a reboot AND the start-race.

    OPNsense firmware actions are async + serialized: a freshly issued action may not have
    flipped upgradestatus to "running" yet, and a just-finished prior action (e.g. a mirror
    check) leaves a stale "done". So a non-"running" status counts as completion only AFTER the
    action has been observed running (or a reboot was seen); a bounded startup grace covers a
    genuinely instant / no-op action that never enters "running"."""
    seen_running = False
    idle_polls = 0
    for _ in range(MAX_STATUS_POLLS):
        try:
            st = await client.firmware_upgrade_status()
        except ReachabilityError:
            await _wait_until_reachable(client)
            seen_running = True  # a reboot means the action was underway
            continue
        if str(st.get("status", "")).lower() == "running":
            seen_running = True
            idle_polls = 0
            await asyncio.sleep(POLL_INTERVAL)
            continue
        if seen_running:
            return st
        idle_polls += 1
        if idle_polls >= STARTUP_GRACE_POLLS:
            return st
        await asyncio.sleep(POLL_INTERVAL)
    raise OpnsenseError("firmware operation did not complete within budget")


async def run_firmware_action(session: AsyncSession, action: FirmwareAction, client, now: datetime) -> str:
    """Execute a firmware action. Returns the new status. Per-device serialized."""
    if action.status not in ("scheduled", "running"):
        return action.status
    got = (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _advisory_key(action.device_id)}
    )).scalar_one()
    if not got:
        return action.status  # another action holds the device lock; leave scheduled for retry
    action.status = "running"
    await session.flush()
    try:
        if action.kind == "plugin_remove":
            await client.plugin_remove(action.target)
            await poll_until_done(client)
        elif action.kind == "plugin_install":
            await client.firmware_check()
            await poll_until_done(client)          # wait for the mirror check to finish (serialized)
            if updates_pending(await client.firmware_status_raw()):
                action.status = "failed"
                action.result = {"error": "device must be up to date before installing plugins"}
                await session.flush()
                return "failed"
            await client.plugin_install(action.target)
            await poll_until_done(client)
        elif action.kind == "firmware_update":
            await client.firmware_update()
            await poll_until_done(client)
        elif action.kind == "firmware_upgrade":
            steps = 0
            for _ in range(MAX_UPGRADE_STEPS):
                await client.firmware_check()
                await poll_until_done(client)      # wait for the mirror check to finish (serialized)
                st = await client.firmware_status_raw()
                if not updates_pending(st) and not major_offered(st):
                    break
                if major_offered(st):
                    await client.firmware_upgrade()
                else:
                    await client.firmware_update()
                await poll_until_done(client)
                steps += 1
            else:
                raise OpnsenseError("upgrade did not converge within MAX_UPGRADE_STEPS")
            action.result = {"steps": steps}
        else:
            action.status = "failed"
            action.result = {"error": f"unknown action kind: {action.kind}"}
            await session.flush()
            return "failed"
        ident = await client.get_device_identity()
        action.status = "done"
        action.applied_at = now
        action.result = {**(action.result or {}), "version": ident.version}
    except OpnsenseError as exc:
        logger.warning("firmware action %s (%s) failed: %s", action.id, action.kind, exc, exc_info=True)
        action.status = "failed"
        action.result = {"error": "action failed"}
    await session.flush()
    return action.status

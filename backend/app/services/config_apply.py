"""Dispatch a config_change to the connector write for its kind.

Each config_change.kind registers an async applier `(client, operation, payload, *, dry_run) -> dict`.
M1's `alias` kind maps to the connector's `apply_alias`; new kinds (M3b+) register their own."""
from collections.abc import Awaitable, Callable

Applier = Callable[..., Awaitable[dict]]


class UnknownChangeKindError(Exception):
    """No applier registered for a config_change kind."""


CHANGE_APPLIERS: dict[str, Applier] = {}


def register_change_applier(change_kind: str, applier: Applier) -> None:
    CHANGE_APPLIERS[change_kind] = applier


async def apply_for_kind(client, change_kind: str, operation: str, payload: dict, *, dry_run: bool) -> dict:
    applier = CHANGE_APPLIERS.get(change_kind)
    if applier is None:
        raise UnknownChangeKindError(f"no applier for config change kind: {change_kind}")
    return await applier(client, operation, payload, dry_run=dry_run)


# --- alias (M1): the verified firewall-alias write ---
async def _apply_alias(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_alias(operation, payload, dry_run=dry_run)


register_change_applier("alias", _apply_alias)

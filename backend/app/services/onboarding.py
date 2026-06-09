from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError


@dataclass
class ProbeResult:
    reachable: bool
    firmware_version: str | None
    error: str | None


async def probe_device(
    base_url: str,
    api_key: str,
    api_secret: str,
    *,
    verify_tls: bool = True,
    tls_fingerprint: str | None = None,
) -> ProbeResult:
    client = OpnsenseClient(base_url, api_key, api_secret, verify_tls=verify_tls)
    try:
        version = await client.test_connection()
        return ProbeResult(reachable=True, firmware_version=version, error=None)
    except OpnsenseError as exc:
        return ProbeResult(
            reachable=False, firmware_version=None, error=f"{type(exc).__name__}: {exc}"
        )


# Tipo del "prober" iniettabile (override-abile nei test degli endpoint).
Prober = Callable[..., Coroutine[Any, Any, ProbeResult]]


def get_prober() -> Prober:
    return probe_device

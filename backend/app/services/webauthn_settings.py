"""RP ID / name / origin for WebAuthn, from runtime settings (env default + DB override). WebAuthn
needs a stable HTTPS domain; until rp_id + origin are set, registration is refused.

These are string settings, so they live in the generic app_settings key/value store (env default +
single DB override row) rather than the numeric runtime_settings registry — the same env-default +
DB-override mechanism as get_live_push / the MFA policy.
"""
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.services.app_settings import get_webauthn_settings


@dataclass(frozen=True)
class WebAuthnConfig:
    rp_id: str
    rp_name: str
    origin: str

    def is_configured(self) -> bool:
        return bool(self.rp_id) and bool(self.origin)


async def get_webauthn_config(session: AsyncSession) -> WebAuthnConfig:
    s = get_settings()
    vals = await get_webauthn_settings(
        session,
        rp_id_default=s.webauthn_rp_id,
        rp_name_default=s.webauthn_rp_name,
        origin_default=s.webauthn_origin,
    )
    return WebAuthnConfig(rp_id=vals["rp_id"], rp_name=vals["rp_name"], origin=vals["origin"])

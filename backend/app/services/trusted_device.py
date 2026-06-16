"""Trusted-device store for "remember this device" — server-side, revocable.

Mirrors the session-token model: a raw token lives only in a cookie; only its
HMAC-SHA256(SESSION_SECRET) hash is stored. find_valid is fail-closed — a garbage,
unknown, expired, or wrong-user token returns None (never grants a skip)."""
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.trusted_device import TrustedDevice


def _hash_token(raw: str) -> str:
    # Same construction as app/services/auth.py:_hash_token — keyed by SESSION_SECRET so a DB dump
    # yields only keyed hashes and rotating the secret invalidates every trusted device.
    return hmac.new(get_settings().session_secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


class TrustedDeviceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_for_user(
        self, user_id: uuid.UUID, *, days: int, user_agent: str | None, ip: str | None
    ) -> tuple[TrustedDevice, str]:
        """Mint a trusted-device row. Returns (row, raw_token); only the hash is stored."""
        now = datetime.now(UTC)
        raw = secrets.token_urlsafe(32)
        row = TrustedDevice(
            user_id=user_id,
            token_hash=_hash_token(raw),
            user_agent=(user_agent[:512] if user_agent else None),
            ip=ip,
            last_used_at=now,
            expires_at=now + timedelta(days=days),
        )
        self.session.add(row)
        await self.session.flush()
        return row, raw

    async def find_valid(self, user_id: uuid.UUID, raw_token: str) -> TrustedDevice | None:
        """The non-expired row for this user matching the token, or None (fail-closed)."""
        if not raw_token:
            return None
        now = datetime.now(UTC)
        row = (
            await self.session.execute(
                select(TrustedDevice).where(TrustedDevice.token_hash == _hash_token(raw_token))
            )
        ).scalar_one_or_none()
        if row is None or row.user_id != user_id or row.expires_at <= now:
            return None
        return row

    async def touch(self, row: TrustedDevice) -> None:
        row.last_used_at = datetime.now(UTC)

    async def list_for_user(self, user_id: uuid.UUID) -> list[TrustedDevice]:
        """Non-expired trusted devices for the user, newest activity first."""
        now = datetime.now(UTC)
        rows = (
            await self.session.execute(
                select(TrustedDevice)
                .where(TrustedDevice.user_id == user_id, TrustedDevice.expires_at > now)
                .order_by(TrustedDevice.last_used_at.desc())
            )
        ).scalars().all()
        return list(rows)

    async def revoke(self, device_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete one device scoped to its owner. Returns True if a row was removed."""
        result = await self.session.execute(
            delete(TrustedDevice).where(
                TrustedDevice.id == device_id, TrustedDevice.user_id == user_id
            )
        )
        return (result.rowcount or 0) > 0

    async def revoke_all(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            delete(TrustedDevice).where(TrustedDevice.user_id == user_id)
        )
        return result.rowcount or 0

    async def purge_expired(self, now: datetime) -> int:
        result = await self.session.execute(
            delete(TrustedDevice).where(TrustedDevice.expires_at <= now)
        )
        return result.rowcount or 0

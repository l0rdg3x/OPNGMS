import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import Row, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog
from app.models.tenant import Tenant
from app.models.user import User

# Defensive cap mirrored by the router (which also validates via Query(le=200)). Kept here so a
# direct repository caller can't ask for an unbounded page either.
MAX_LIMIT = 200


class AuditRepository:
    """Global (non-RLS) audit-log reads with actor->email and tenant->name enrichment.

    The audit_log table is intentionally NOT tenant-scoped: it is the cross-tenant ledger. Access
    control lives entirely in the route (superadmin-only via require_org(AUDIT_VIEW)), so callers of
    this repository must already be gated.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _apply_filters(
        self,
        stmt: Select,
        *,
        actor_user_id: uuid.UUID | None,
        tenant_id: uuid.UUID | None,
        action: str | None,
        frm: datetime | None,
        to: datetime | None,
    ) -> Select:
        if actor_user_id is not None:
            stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
        if tenant_id is not None:
            stmt = stmt.where(AuditLog.tenant_id == tenant_id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)  # exact match (no LIKE)
        if frm is not None:
            stmt = stmt.where(AuditLog.ts >= frm)
        if to is not None:
            stmt = stmt.where(AuditLog.ts < to)
        return stmt

    def _enriched_select(self) -> Select:
        """Rows of (AuditLog, User.email, Tenant.name) — outer joins so NULL/orphan actor or tenant
        still yields the row, with a NULL email / name."""
        return (
            select(AuditLog, User.email, Tenant.name)
            .outerjoin(User, AuditLog.actor_user_id == User.id)
            .outerjoin(Tenant, AuditLog.tenant_id == Tenant.id)
        )

    async def query(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        action: str | None = None,
        frm: datetime | None = None,
        to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Row], int]:
        capped = max(1, min(limit, MAX_LIMIT))
        filters = {
            "actor_user_id": actor_user_id,
            "tenant_id": tenant_id,
            "action": action,
            "frm": frm,
            "to": to,
        }
        page = self._apply_filters(self._enriched_select(), **filters)
        page = page.order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(capped).offset(max(0, offset))
        rows = (await self.session.execute(page)).all()

        count_stmt = self._apply_filters(select(func.count()).select_from(AuditLog), **filters)
        total = (await self.session.execute(count_stmt)).scalar_one()
        return list(rows), int(total)

    async def stream(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        action: str | None = None,
        frm: datetime | None = None,
        to: datetime | None = None,
    ) -> AsyncIterator[Row]:
        """Yield enriched rows (no pagination) in the same order as ``query`` — used for CSV export."""
        stmt = self._apply_filters(
            self._enriched_select(),
            actor_user_id=actor_user_id,
            tenant_id=tenant_id,
            action=action,
            frm=frm,
            to=to,
        ).order_by(AuditLog.ts.desc(), AuditLog.id.desc())
        result = await self.session.stream(stmt)
        async for row in result:
            yield row

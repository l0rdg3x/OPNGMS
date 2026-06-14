import csv
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import require_org
from app.core.rbac import Action
from app.models.user import User
from app.repositories.audit import AuditRepository
from app.schemas.audit import AuditEntryOut, AuditListOut

router = APIRouter(prefix="/api/admin/audit", tags=["audit"])

# CSV column order; mirrors AuditEntryOut for a stable, predictable export header.
_CSV_HEADER = [
    "ts",
    "actor_user_id",
    "actor_email",
    "tenant_id",
    "tenant_name",
    "action",
    "target_type",
    "target_id",
    "ip",
    "details",
]


def _to_entry(row) -> AuditEntryOut:
    """Map a (AuditLog, email, name) joined row to the API shape. email/name are NULL when the
    actor/tenant is absent (NULL FK) or was deleted (orphan id)."""
    log, email, name = row
    return AuditEntryOut(
        id=log.id,
        ts=log.ts,
        actor_user_id=log.actor_user_id,
        actor_email=email,
        tenant_id=log.tenant_id,
        tenant_name=name,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        ip=log.ip,
        details=log.details or {},
    )


@router.get("", response_model=AuditListOut)
async def list_audit(
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = Query(None, max_length=200),
    tenant_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=100),
    frm: datetime | None = None,
    to: datetime | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_org(Action.AUDIT_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> AuditListOut:
    rows, total = await AuditRepository(session).query(
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        tenant_id=tenant_id,
        action=action,
        frm=frm,
        to=to,
        limit=limit,
        offset=offset,
    )
    return AuditListOut(items=[_to_entry(r) for r in rows], total=total)


@router.get("/export.csv")
async def export_audit(
    actor_user_id: uuid.UUID | None = None,
    actor_email: str | None = Query(None, max_length=200),
    tenant_id: uuid.UUID | None = None,
    action: str | None = Query(None, max_length=100),
    frm: datetime | None = None,
    to: datetime | None = None,
    user: User = Depends(require_org(Action.AUDIT_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    async def _rows() -> AsyncIterator[str]:
        buf = io.StringIO()
        writer = csv.writer(buf)

        def _take() -> str:
            chunk = buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            return chunk

        writer.writerow(_CSV_HEADER)
        yield _take()
        async for row in AuditRepository(session).stream(
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            tenant_id=tenant_id,
            action=action,
            frm=frm,
            to=to,
        ):
            e = _to_entry(row)
            writer.writerow(
                [
                    e.ts.isoformat(),
                    str(e.actor_user_id) if e.actor_user_id else "",
                    e.actor_email or "",
                    str(e.tenant_id) if e.tenant_id else "",
                    e.tenant_name or "",
                    e.action,
                    e.target_type or "",
                    e.target_id or "",
                    e.ip or "",
                    json.dumps(e.details, default=str),
                ]
            )
            yield _take()

    return StreamingResponse(
        _rows(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit.csv"'},
    )

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.metric import MetricPoint

# Defensive cap: maximum number of rows returned by the raw series (without bucket),
# to avoid materializing unbounded series.
MAX_POINTS = 5000


class MetricRepository:
    """Per-tenant time-series reads. Two isolation layers: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    @staticmethod
    def _to_points(rows) -> list[MetricPoint]:
        return [
            MetricPoint(time=r.point_time, label=r.label, value=float(r.point_value))
            for r in rows
        ]

    async def series(
        self,
        device_id: uuid.UUID,
        metric: str,
        frm: datetime,
        to: datetime,
        bucket: timedelta | None,
    ) -> list[MetricPoint]:
        params = {
            "tid": self.tenant_id,
            "did": device_id,
            "metric": metric,
            "frm": frm,
            "to": to,
        }
        if bucket is not None:
            params["bucket"] = bucket
            sql = text(
                "SELECT time_bucket(:bucket, time) AS point_time, label, avg(value) AS point_value "
                "FROM metrics "
                "WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
                "  AND time >= :frm AND time < :to "
                "GROUP BY point_time, label ORDER BY point_time, label"
            )
        else:
            # Defensive cap: without a bucket we limit the raw rows to MAX_POINTS,
            # selecting the most recent within MAX_POINTS, presented in ascending order.
            params["limit"] = MAX_POINTS
            sql = text(
                "SELECT point_time, label, point_value FROM ("
                "  SELECT time AS point_time, label, value AS point_value "
                "  FROM metrics "
                "  WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
                "    AND time >= :frm AND time < :to "
                "  ORDER BY time DESC "
                "  LIMIT :limit"
                ") sub "
                "ORDER BY point_time, label"
            )
        rows = (await self.session.execute(sql, params)).all()
        return self._to_points(rows)

    async def last(self, device_id: uuid.UUID, metric: str) -> list[MetricPoint]:
        sql = text(
            "SELECT DISTINCT ON (label) time AS point_time, label, value AS point_value "
            "FROM metrics "
            "WHERE tenant_id = :tid AND device_id = :did AND metric = :metric "
            "ORDER BY label, time DESC"
        )
        rows = (
            await self.session.execute(
                sql, {"tid": self.tenant_id, "did": device_id, "metric": metric}
            )
        ).all()
        return self._to_points(rows)

"""Usage logging and statistics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.logging import get_logger
from app.models.usage_log import UsageLog
from app.schemas.account import DailyUsage, EndpointUsage, UsageStats

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)


class UsageService:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def record(
        self,
        *,
        account_id: UUID,
        api_key_id: UUID | None,
        endpoint: str,
        request_id: str | None,
        status_code: int,
        processing_time_ms: float,
        units: int = 1,
        cached: bool = False,
    ) -> None:
        try:
            async with self.session_factory() as session:
                session.add(
                    UsageLog(
                        account_id=account_id,
                        api_key_id=api_key_id,
                        endpoint=endpoint,
                        request_id=request_id,
                        status_code=status_code,
                        processing_time_ms=processing_time_ms,
                        units=units,
                        cached=cached,
                    )
                )
                await session.commit()
        except SQLAlchemyError:
            logger.warning("usage_log_write_failed", exc_info=True)

    async def stats(self, account_id: UUID, *, days: int = 30) -> tuple[UsageStats, datetime, datetime]:
        days = max(1, min(days, 365))
        period_end = datetime.now(UTC)
        period_start = period_end - timedelta(days=days)
        error_case = case((UsageLog.status_code >= 400, 1), else_=0)
        try:
            async with self.session_factory() as session:
                totals = (
                    await session.execute(
                        select(
                            func.count(UsageLog.id),
                            func.coalesce(func.sum(error_case), 0),
                            func.coalesce(func.sum(case((UsageLog.cached.is_(True), 1), else_=0)), 0),
                            func.coalesce(func.sum(UsageLog.units), 0),
                        ).where(
                            UsageLog.account_id == account_id,
                            UsageLog.created_at >= period_start,
                        )
                    )
                ).one()
                endpoint_rows = (
                    await session.execute(
                        select(
                            UsageLog.endpoint,
                            func.count(UsageLog.id),
                            func.coalesce(func.sum(error_case), 0),
                            func.coalesce(func.avg(UsageLog.processing_time_ms), 0.0),
                        )
                        .where(
                            UsageLog.account_id == account_id,
                            UsageLog.created_at >= period_start,
                        )
                        .group_by(UsageLog.endpoint)
                        .order_by(func.count(UsageLog.id).desc())
                    )
                ).all()
                daily_rows = (
                    await session.execute(
                        select(
                            func.date(UsageLog.created_at),
                            func.count(UsageLog.id),
                            func.coalesce(func.sum(error_case), 0),
                        )
                        .where(
                            UsageLog.account_id == account_id,
                            UsageLog.created_at >= period_start,
                        )
                        .group_by(func.date(UsageLog.created_at))
                        .order_by(func.date(UsageLog.created_at))
                    )
                ).all()
        except SQLAlchemyError:
            logger.warning("usage_stats_query_failed", exc_info=True)
            return UsageStats(), period_start, period_end
        stats = UsageStats(
            total_requests=int(totals[0] or 0),
            error_requests=int(totals[1] or 0),
            cached_requests=int(totals[2] or 0),
            total_units=int(totals[3] or 0),
            by_endpoint=[
                EndpointUsage(
                    endpoint=str(row[0]),
                    requests=int(row[1] or 0),
                    errors=int(row[2] or 0),
                    average_latency_ms=round(float(row[3] or 0.0), 2),
                )
                for row in endpoint_rows
            ],
            daily=[
                DailyUsage(
                    date=str(row[0]),
                    requests=int(row[1] or 0),
                    errors=int(row[2] or 0),
                )
                for row in daily_rows
            ],
        )
        return stats, period_start, period_end

    async def today_count(self, account_id: UUID) -> int:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(func.count(UsageLog.id)).where(
                        UsageLog.account_id == account_id,
                        UsageLog.created_at >= day_start,
                    )
                )
                return int(result.scalar_one() or 0)
        except SQLAlchemyError:
            return 0

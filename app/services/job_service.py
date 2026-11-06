"""Background job persistence and ARQ enqueueing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.core.exceptions import DatabaseUnavailableError, NotFoundError, WorkerUnavailableError
from app.core.logging import get_logger
from app.models.extraction_job import ExtractionJob
from app.schemas.job import JobStatus, JobStatusResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = get_logger(__name__)


class JobService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        arq_pool: Any | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self._arq_pool = arq_pool

    async def create_job(
        self,
        *,
        account_id: UUID,
        api_key_id: UUID | None,
        job_type: str,
        payload: dict[str, Any],
        request_id: str | None,
        function_name: str,
        queue_name: str,
    ) -> ExtractionJob:
        try:
            async with self.session_factory() as session:
                job = ExtractionJob(
                    account_id=account_id,
                    api_key_id=api_key_id,
                    job_type=job_type,
                    status=JobStatus.QUEUED.value,
                    request_id=request_id,
                    payload=payload,
                )
                session.add(job)
                await session.commit()
                await session.refresh(job)
        except SQLAlchemyError as exc:
            raise DatabaseUnavailableError("job could not be persisted") from exc
        try:
            await self._enqueue(job.id, function_name=function_name, queue_name=queue_name)
        except Exception as exc:
            await self.mark_failed(job.id, "background queue is unavailable")
            raise WorkerUnavailableError("background queue is unavailable") from exc
        return job

    async def _enqueue(self, job_id: UUID, *, function_name: str, queue_name: str) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        close_pool = False
        pool = self._arq_pool
        if pool is None:
            pool = await create_pool(RedisSettings.from_dsn(self.settings.redis_url))
            close_pool = True
        try:
            await pool.enqueue_job(
                function_name,
                str(job_id),
                _job_id=str(job_id),
                _queue_name=queue_name,
            )
        finally:
            if close_pool:
                await pool.aclose()

    async def load(self, job_id: UUID) -> ExtractionJob | None:
        try:
            async with self.session_factory() as session:
                return await session.get(ExtractionJob, job_id)
        except SQLAlchemyError:
            logger.warning("job_load_failed", extra={"job_id": str(job_id)}, exc_info=True)
            return None

    async def get_job(self, job_id: UUID, *, account_id: UUID) -> ExtractionJob:
        try:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(ExtractionJob).where(
                        ExtractionJob.id == job_id,
                        ExtractionJob.account_id == account_id,
                    )
                )
                job = result.scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise DatabaseUnavailableError("job store is unavailable") from exc
        if job is None:
            raise NotFoundError("job not found")
        return job

    async def mark_running(self, job_id: UUID) -> None:
        await self._update(
            job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
            increment_attempts=True,
        )

    async def mark_succeeded(self, job_id: UUID, result: Any) -> None:
        await self._update(
            job_id,
            status=JobStatus.SUCCEEDED,
            result=result,
            finished_at=datetime.now(UTC),
            clear_error=True,
        )

    async def mark_failed(self, job_id: UUID, error: str) -> None:
        await self._update(
            job_id,
            status=JobStatus.FAILED,
            error=error[:2000],
            finished_at=datetime.now(UTC),
        )

    async def _update(
        self,
        job_id: UUID,
        *,
        status: JobStatus,
        result: Any = None,
        error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        increment_attempts: bool = False,
        clear_error: bool = False,
    ) -> None:
        try:
            async with self.session_factory() as session:
                job = await session.get(ExtractionJob, job_id)
                if job is None:
                    return
                job.status = status.value
                if result is not None:
                    job.result = result
                if error is not None:
                    job.error = error
                if clear_error:
                    job.error = None
                if started_at is not None:
                    job.started_at = started_at
                if finished_at is not None:
                    job.finished_at = finished_at
                if increment_attempts:
                    job.attempts += 1
                await session.commit()
        except SQLAlchemyError:
            logger.warning("job_status_update_failed", extra={"job_id": str(job_id)}, exc_info=True)

    @staticmethod
    def to_response(job: ExtractionJob) -> JobStatusResponse:
        return JobStatusResponse(
            job_id=job.id,
            status=JobStatus(job.status),
            job_type=job.job_type,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            attempts=job.attempts,
            result=job.result,
            error=job.error,
        )

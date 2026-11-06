"""Background job status endpoint."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies import CurrentAuth, get_job_service
from app.schemas.job import JobStatusResponse
from app.services.job_service import JobService

router = APIRouter(tags=["jobs"])


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Get background job status",
)
async def get_job(
    job_id: UUID,
    auth: CurrentAuth,
    jobs: JobService = Depends(get_job_service),
) -> JobStatusResponse:
    job = await jobs.get_job(job_id, account_id=auth.account_id)
    return JobService.to_response(job)

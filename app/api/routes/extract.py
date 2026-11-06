"""Single and batch extraction endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    CurrentAuth,
    get_extraction_service,
    get_job_service,
)
from app.config import Settings
from app.schemas.extraction import (
    BatchExtractionRequest,
    BatchExtractionResponse,
    ExtractionRequest,
    ExtractionResponse,
)
from app.schemas.job import JobAcceptedResponse, JobStatus
from app.services.extraction_service import ExtractionService
from app.services.job_service import JobService

router = APIRouter(tags=["extraction"])


def _job_target(allow_browser: bool, settings: Settings) -> tuple[str, str]:
    if allow_browser:
        return "process_browser_job", settings.worker_browser_queue
    return "process_extraction_job", settings.worker_extraction_queue


async def _enqueue(
    *,
    request: Request,
    auth: CurrentAuth,
    jobs: JobService,
    settings: Settings,
    payload: dict[str, object],
    allow_browser: bool,
) -> JSONResponse:
    function_name, queue_name = _job_target(allow_browser, settings)
    job = await jobs.create_job(
        account_id=auth.account_id,
        api_key_id=auth.api_key_id,
        job_type="extraction",
        payload=payload,
        request_id=getattr(request.state, "request_id", None),
        function_name=function_name,
        queue_name=queue_name,
    )
    accepted = JobAcceptedResponse(
        job_id=job.id,
        status=JobStatus.QUEUED,
        status_url=f"{settings.api_v1_prefix}/jobs/{job.id}",
    )
    return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))


@router.post(
    "/extract",
    response_model=ExtractionResponse,
    summary="Extract a single URL",
    responses={202: {"model": JobAcceptedResponse, "description": "Background job accepted"}},
)
async def extract(
    payload: ExtractionRequest,
    request: Request,
    auth: CurrentAuth,
    service: ExtractionService = Depends(get_extraction_service),
    jobs: JobService = Depends(get_job_service),
) -> ExtractionResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    if payload.async_job:
        return await _enqueue(
            request=request,
            auth=auth,
            jobs=jobs,
            settings=settings,
            allow_browser=payload.allow_browser,
            payload={
                "urls": [payload.url],
                "include_text": payload.include_text,
                "include_media": payload.include_media,
                "allow_browser": payload.allow_browser,
                "use_cache": payload.use_cache,
                "force_refresh": payload.force_refresh,
                "timeout_seconds": (payload.timeout_ms / 1000) if payload.timeout_ms else None,
                "request_id": request_id,
            },
        )
    response = await service.extract_url(
        payload.url,
        include_text=payload.include_text,
        include_media=payload.include_media,
        allow_browser=payload.allow_browser,
        use_cache=payload.use_cache,
        force_refresh=payload.force_refresh,
        timeout=(payload.timeout_ms / 1000) if payload.timeout_ms else None,
        request_id=request_id,
    )
    request.state.cached = response.cached
    return response


@router.post(
    "/extract/batch",
    response_model=BatchExtractionResponse,
    summary="Extract multiple URLs",
    responses={202: {"model": JobAcceptedResponse, "description": "Background job accepted"}},
)
async def extract_batch(
    payload: BatchExtractionRequest,
    request: Request,
    auth: CurrentAuth,
    service: ExtractionService = Depends(get_extraction_service),
    jobs: JobService = Depends(get_job_service),
) -> BatchExtractionResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    request_id = getattr(request.state, "request_id", None)
    if payload.async_job:
        return await _enqueue(
            request=request,
            auth=auth,
            jobs=jobs,
            settings=settings,
            allow_browser=payload.allow_browser,
            payload={
                "urls": payload.urls,
                "include_text": payload.include_text,
                "include_media": payload.include_media,
                "allow_browser": payload.allow_browser,
                "use_cache": payload.use_cache,
                "force_refresh": payload.force_refresh,
                "timeout_ms": payload.timeout_ms,
                "request_id": request_id,
            },
        )
    return await service.extract_batch(
        payload.urls,
        include_text=payload.include_text,
        include_media=payload.include_media,
        allow_browser=payload.allow_browser,
        use_cache=payload.use_cache,
        force_refresh=payload.force_refresh,
        timeout_ms=payload.timeout_ms,
        request_id=request_id,
    )

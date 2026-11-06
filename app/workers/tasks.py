"""ARQ task functions shared by worker entry points."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.schemas.search import SearchRequest
from app.workers.runtime import WorkerRuntime, runtime_from_ctx

logger = get_logger(__name__)


async def _handle_failure(
    ctx: dict[Any, Any],
    runtime: WorkerRuntime,
    job_id: UUID,
    exc: Exception,
) -> None:
    attempts = int(ctx.get("job_try", 1))
    max_attempts = runtime.settings.job_max_retries + 1
    retryable = isinstance(exc, AppError) and exc.retryable
    if retryable and attempts < max_attempts:
        logger.warning(
            "job_retry_scheduled",
            extra={"job_id": str(job_id), "attempt": attempts},
        )
        raise exc
    message = exc.message if isinstance(exc, AppError) else "background job failed"
    await runtime.jobs.mark_failed(job_id, message)


async def process_search_job(ctx: dict[Any, Any], job_id: str) -> dict[str, Any]:
    runtime = runtime_from_ctx(ctx)
    identifier = UUID(job_id)
    await runtime.jobs.mark_running(identifier)
    try:
        job = await runtime.jobs.load(identifier)
        if job is None:
            raise AppError("job record disappeared")
        payload = job.payload or {}
        request = SearchRequest.model_validate(payload["request"]).model_copy(update={"async_job": False})
        response = await runtime.orchestrator.search(
            request,
            request_id=payload.get("request_id") or job.request_id,
            account_id=payload.get("account_id") or str(job.account_id),
        )
        result = response.model_dump(mode="json")
        await runtime.jobs.mark_succeeded(identifier, result)
        return {"job_id": job_id, "status": "succeeded"}
    except Exception as exc:
        await _handle_failure(ctx, runtime, identifier, exc)
        return {"job_id": job_id, "status": "failed"}
    finally:
        if ctx.get("job_try", 1) >= runtime.settings.job_max_retries + 1:
            logger.info("job_finished", extra={"job_id": job_id})


async def _process_extraction(ctx: dict[Any, Any], job_id: str, *, force_browser: bool) -> dict[str, Any]:
    runtime = runtime_from_ctx(ctx)
    identifier = UUID(job_id)
    await runtime.jobs.mark_running(identifier)
    try:
        job = await runtime.jobs.load(identifier)
        if job is None:
            raise AppError("job record disappeared")
        payload = job.payload or {}
        urls: list[str] = payload.get("urls") or ([payload["url"]] if payload.get("url") else [])
        allow_browser = bool(payload.get("allow_browser", False)) or force_browser
        if not urls:
            raise AppError("job payload contains no URLs")
        if len(urls) == 1:
            response = await runtime.extraction.extract_url(
                urls[0],
                include_text=bool(payload.get("include_text", True)),
                include_media=bool(payload.get("include_media", True)),
                allow_browser=allow_browser,
                use_cache=bool(payload.get("use_cache", True)),
                force_refresh=bool(payload.get("force_refresh", False)),
                timeout=float(payload.get("timeout_seconds") or runtime.settings.extraction_timeout),
                request_id=payload.get("request_id") or job.request_id,
            )
            result = response.model_dump(mode="json")
        else:
            batch = await runtime.extraction.extract_batch(
                urls,
                include_text=bool(payload.get("include_text", True)),
                include_media=bool(payload.get("include_media", True)),
                allow_browser=allow_browser,
                use_cache=bool(payload.get("use_cache", True)),
                force_refresh=bool(payload.get("force_refresh", False)),
                timeout_ms=payload.get("timeout_ms"),
                request_id=payload.get("request_id") or job.request_id,
            )
            result = batch.model_dump(mode="json")
        await runtime.jobs.mark_succeeded(identifier, result)
        return {"job_id": job_id, "status": "succeeded"}
    except Exception as exc:
        await _handle_failure(ctx, runtime, identifier, exc)
        return {"job_id": job_id, "status": "failed"}


async def process_extraction_job(ctx: dict[Any, Any], job_id: str) -> dict[str, Any]:
    return await _process_extraction(ctx, job_id, force_browser=False)


async def process_browser_job(ctx: dict[Any, Any], job_id: str) -> dict[str, Any]:
    return await _process_extraction(ctx, job_id, force_browser=True)

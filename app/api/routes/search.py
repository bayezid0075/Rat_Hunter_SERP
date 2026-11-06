"""Search endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import CurrentAuth, get_job_service, get_orchestrator
from app.config import Settings
from app.core.orchestrator import SearchOrchestrator
from app.schemas.job import JobAcceptedResponse, JobStatus
from app.schemas.product import PriceData
from app.schemas.search import (
    ProductSearchRequest,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from app.services.job_service import JobService

router = APIRouter(tags=["search"])


async def _run_or_enqueue(
    *,
    request: Request,
    payload: SearchRequest,
    auth: CurrentAuth,
    orchestrator: SearchOrchestrator,
    jobs: JobService,
    settings: Settings,
) -> SearchResponse | JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    if payload.async_job:
        job = await jobs.create_job(
            account_id=auth.account_id,
            api_key_id=auth.api_key_id,
            job_type="search",
            payload={
                "request": payload.model_dump(mode="json"),
                "account_id": str(auth.account_id),
                "request_id": request_id,
            },
            request_id=request_id,
            function_name="process_search_job",
            queue_name=settings.worker_search_queue,
        )
        accepted = JobAcceptedResponse(
            job_id=job.id,
            status=JobStatus.QUEUED,
            status_url=f"{settings.api_v1_prefix}/jobs/{job.id}",
        )
        return JSONResponse(status_code=202, content=accepted.model_dump(mode="json"))
    response = await orchestrator.search(
        payload,
        request_id=request_id,
        account_id=str(auth.account_id),
    )
    request.state.cached = response.metadata.cached
    return response


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Search the web",
    responses={202: {"model": JobAcceptedResponse, "description": "Background job accepted"}},
)
async def search(
    payload: SearchRequest,
    request: Request,
    auth: CurrentAuth,
    orchestrator: SearchOrchestrator = Depends(get_orchestrator),
    jobs: JobService = Depends(get_job_service),
) -> SearchResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    return await _run_or_enqueue(
        request=request,
        payload=payload,
        auth=auth,
        orchestrator=orchestrator,
        jobs=jobs,
        settings=settings,
    )


def _result_price(result: SearchResult) -> PriceData | None:
    if result.snippet_price is not None:
        return result.snippet_price
    if result.extraction and result.extraction.product:
        return result.extraction.product.price
    return None


def _filter_product_prices(response: SearchResponse, payload: ProductSearchRequest) -> SearchResponse:
    if payload.min_price is None and payload.max_price is None and payload.currency is None:
        return response
    filtered: list[SearchResult] = []
    for result in response.results:
        price = _result_price(result)
        if price is None:
            filtered.append(result)
            continue
        if payload.currency and price.currency != payload.currency.upper():
            continue
        if payload.min_price is not None and price.amount < payload.min_price:
            continue
        if payload.max_price is not None and price.amount > payload.max_price:
            continue
        filtered.append(result)
    for index, result in enumerate(filtered, start=1):
        result.position = index
    response.results = filtered
    response.metadata.total_results = len(filtered)
    return response


@router.post(
    "/products/search",
    response_model=SearchResponse,
    summary="Product-focused search",
    responses={202: {"model": JobAcceptedResponse, "description": "Background job accepted"}},
)
async def product_search(
    payload: ProductSearchRequest,
    request: Request,
    auth: CurrentAuth,
    orchestrator: SearchOrchestrator = Depends(get_orchestrator),
    jobs: JobService = Depends(get_job_service),
) -> SearchResponse | JSONResponse:
    settings: Settings = request.app.state.settings
    result = await _run_or_enqueue(
        request=request,
        payload=payload,
        auth=auth,
        orchestrator=orchestrator,
        jobs=jobs,
        settings=settings,
    )
    if isinstance(result, SearchResponse):
        return _filter_product_prices(result, payload)
    return result

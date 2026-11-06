"""Search source discovery and health endpoints."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.search.registry import AdapterRegistry
from app.api.dependencies import CurrentAuth, get_cache, get_registry
from app.core.cache import CacheManager
from app.core.logging import get_logger
from app.models.source_health import SourceHealth
from app.schemas.common import SourceHealthStatus
from app.schemas.source import SourceHealthItem, SourceHealthResponse, SourceInfo

router = APIRouter(tags=["sources"])
logger = get_logger(__name__)
_HEALTH_CACHE_KEY = "serp:source-health:all"


@router.get("/sources", response_model=list[SourceInfo], summary="Configured search sources")
async def list_sources(
    _: CurrentAuth,
    registry: AdapterRegistry = Depends(get_registry),
) -> list[SourceInfo]:
    return [SourceInfo.model_validate(info) for info in (a.info() for a in registry.all())]


@router.get(
    "/sources/health",
    response_model=SourceHealthResponse,
    summary="Search source health",
)
async def sources_health(
    request: Request,
    _: CurrentAuth,
    registry: AdapterRegistry = Depends(get_registry),
    cache: CacheManager = Depends(get_cache),
) -> SourceHealthResponse:
    settings = request.app.state.settings

    async def check_all() -> list[dict[str, object]]:
        async def one(adapter: object) -> SourceHealthItem:
            name = getattr(adapter, "name", "unknown")
            started = time.perf_counter()
            try:
                health = await asyncio.wait_for(
                    adapter.health_check(),  # type: ignore[attr-defined]
                    timeout=settings.adapter_timeout,
                )
                return SourceHealthItem(
                    name=name,
                    status=health.status,
                    latency_ms=health.latency_ms
                    if health.latency_ms is not None
                    else round((time.perf_counter() - started) * 1000, 2),
                    error=health.error,
                )
            except TimeoutError:
                return SourceHealthItem(
                    name=name,
                    status=SourceHealthStatus.TIMEOUT,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    error="health check timed out",
                )
            except Exception as exc:
                return SourceHealthItem(
                    name=name,
                    status=SourceHealthStatus.UNAVAILABLE,
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    error=exc.__class__.__name__,
                )

        items = await asyncio.gather(*(one(adapter) for adapter in registry.all()))
        if request.app.state.settings.cache_backend == "redis":
            try:
                async with request.app.state.db.session_factory() as session:
                    for item in items:
                        session.add(
                            SourceHealth(
                                source_name=item.name,
                                status=item.status.value,
                                latency_ms=item.latency_ms,
                                error=item.error,
                            )
                        )
                    await session.commit()
            except SQLAlchemyError:
                logger.warning("source_health_persist_failed", exc_info=True)
        return [item.model_dump(mode="json") for item in items]

    entry = await cache.get_or_set(
        _HEALTH_CACHE_KEY,
        check_all,
        fresh_ttl=settings.source_health_ttl,
        stale_ttl=settings.source_health_ttl * 2,
    )
    items = [SourceHealthItem.model_validate(item) for item in entry.value]
    return SourceHealthResponse(sources=items)

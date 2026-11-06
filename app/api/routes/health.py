"""Liveness and readiness endpoints."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.monitoring.metrics import DB_HEALTH, REDIS_HEALTH
from app.schemas.common import (
    ComponentHealth,
    HealthResponse,
    ReadyResponse,
    SourceHealthStatus,
)

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Application liveness")
async def health(request: Request) -> HealthResponse:
    settings = request.app.state.settings
    started = getattr(request.app.state, "started_monotonic", time.monotonic())
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
        uptime_seconds=round(time.monotonic() - started, 2),
        components={"api": ComponentHealth(status=SourceHealthStatus.OK)},
    )


@router.get("/ready", response_model=ReadyResponse, summary="Readiness check")
async def ready(request: Request) -> ReadyResponse | JSONResponse:
    db = request.app.state.db
    cache = request.app.state.cache
    db_ok = await db.ping()
    cache_ok = await cache.ping()
    DB_HEALTH.set(1 if db_ok else 0)
    REDIS_HEALTH.set(1 if cache_ok else 0)
    checks = {"database": db_ok, "cache": cache_ok}
    response = ReadyResponse(ready=all(checks.values()), checks=checks)
    if not response.ready:
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    return response

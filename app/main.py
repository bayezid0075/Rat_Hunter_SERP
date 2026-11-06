"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.adapters.search.registry import build_registry
from app.api.middleware import (
    AccessLogMiddleware,
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
)
from app.api.routes import account, extract, health, jobs, search, sources
from app.config import Settings, get_settings
from app.core.cache import CacheManager
from app.core.concurrency import ConcurrencyManager
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.orchestrator import SearchOrchestrator
from app.core.rate_limit import RateLimiter
from app.database.session import Database
from app.extractors.browser import BrowserPool
from app.extractors.http import HttpFetcher
from app.extractors.pipeline import ExtractionPipeline
from app.extractors.plugins import build_extraction_plugins
from app.monitoring.metrics import metrics_response
from app.monitoring.sentry import init_sentry
from app.processors.ranking import RankingEngine
from app.services.auth_service import AuthService
from app.services.extraction_service import ExtractionService
from app.services.job_service import JobService
from app.services.usage_service import UsageService

logger = get_logger(__name__)


def _existing(app: FastAPI, name: str) -> Any | None:
    return getattr(app.state, name, None)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.log_json)
    init_sentry(settings)

    db = _existing(app, "db") or Database(settings)
    cache = _existing(app, "cache") or CacheManager.from_settings(settings)
    http_fetcher = _existing(app, "http_fetcher") or HttpFetcher(settings)
    browser_pool = _existing(app, "browser_pool") or BrowserPool(settings)
    concurrency = _existing(app, "concurrency") or ConcurrencyManager(settings, cache)
    rate_limiter = _existing(app, "rate_limiter") or RateLimiter.from_settings(settings, cache)

    pipeline = _existing(app, "pipeline") or ExtractionPipeline(settings, plugin_registry=build_extraction_plugins())
    extraction_service = _existing(app, "extraction_service") or ExtractionService(
        settings=settings,
        cache=cache,
        concurrency=concurrency,
        http_fetcher=http_fetcher,
        browser_pool=browser_pool,
        pipeline=pipeline,
    )
    registry = _existing(app, "registry") or build_registry(
        settings,
        http_fetcher=http_fetcher,
        browser_pool=browser_pool,
        session_factory=db.session_factory,
    )
    ranking = RankingEngine(settings.ranking_weights)
    orchestrator = _existing(app, "orchestrator") or SearchOrchestrator(
        settings=settings,
        registry=registry,
        cache=cache,
        concurrency=concurrency,
        ranking=ranking,
        session_factory=db.session_factory,
        extraction_service=extraction_service,
    )
    auth_service = AuthService(settings=settings, cache=cache, rate_limiter=rate_limiter)
    usage_service = UsageService(session_factory=db.session_factory)
    job_service = _existing(app, "job_service") or JobService(settings=settings, session_factory=db.session_factory)

    app.state.db = db
    app.state.cache = cache
    app.state.http_fetcher = http_fetcher
    app.state.browser_pool = browser_pool
    app.state.concurrency = concurrency
    app.state.rate_limiter = rate_limiter
    app.state.pipeline = pipeline
    app.state.extraction_service = extraction_service
    app.state.registry = registry
    app.state.orchestrator = orchestrator
    app.state.auth_service = auth_service
    app.state.usage_service = usage_service
    app.state.job_service = job_service
    app.state.started_monotonic = time.monotonic()

    db_ok = await db.ping()
    cache_ok = await cache.ping()
    if not db_ok:
        logger.warning("database_not_ready_at_startup")
    if not cache_ok:
        logger.warning("cache_not_ready_at_startup")
    logger.info(
        "application_started",
        extra={
            "environment": settings.environment,
            "adapters": registry.names(),
            "cache_backend": settings.cache_backend,
        },
    )
    try:
        yield
    finally:
        try:
            await registry.close()
            await browser_pool.close()
            await http_fetcher.aclose()
            await cache.aclose()
            await db.dispose()
        finally:
            logger.info("application_stopped")


def create_app(
    settings: Settings | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "Independent, provider-agnostic search discovery, SERP collection, extraction and structured data API."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
        openapi_tags=[
            {"name": "search", "description": "Search discovery and product search."},
            {"name": "extraction", "description": "Universal page extraction."},
            {"name": "jobs", "description": "Asynchronous background jobs."},
            {"name": "account", "description": "Account and API usage."},
            {"name": "sources", "description": "Search source discovery and health."},
            {"name": "health", "description": "Liveness and readiness."},
        ],
    )
    app.state.settings = settings
    for key, value in (overrides or {}).items():
        setattr(app.state, key, value)

    app.add_middleware(AccessLogMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
            expose_headers=[settings.request_id_header],
        )
    if settings.allowed_hosts and settings.allowed_hosts != ["*"]:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    register_exception_handlers(app)

    prefix = settings.api_v1_prefix
    app.include_router(search.router, prefix=prefix)
    app.include_router(extract.router, prefix=prefix)
    app.include_router(jobs.router, prefix=prefix)
    app.include_router(health.router, prefix=prefix)
    app.include_router(account.router, prefix=prefix)
    app.include_router(sources.router, prefix=prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": f"{prefix}/health",
        }

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Any:
            return metrics_response()

    return app


app = create_app()

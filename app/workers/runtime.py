"""Shared worker runtime construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.adapters.search.registry import AdapterRegistry, build_registry
from app.config import Settings
from app.core.cache import CacheManager
from app.core.concurrency import ConcurrencyManager
from app.core.orchestrator import SearchOrchestrator
from app.database.session import Database
from app.extractors.browser import BrowserPool
from app.extractors.http import HttpFetcher
from app.extractors.pipeline import ExtractionPipeline
from app.extractors.plugins import build_extraction_plugins
from app.processors.ranking import RankingEngine
from app.services.extraction_service import ExtractionService
from app.services.job_service import JobService


@dataclass(slots=True)
class WorkerRuntime:
    settings: Settings
    db: Database
    cache: CacheManager
    concurrency: ConcurrencyManager
    http: HttpFetcher
    browser: BrowserPool
    registry: AdapterRegistry
    pipeline: ExtractionPipeline
    extraction: ExtractionService
    orchestrator: SearchOrchestrator
    jobs: JobService

    async def close(self) -> None:
        await self.registry.close()
        await self.browser.close()
        await self.http.aclose()
        await self.cache.aclose()
        await self.db.dispose()


async def build_runtime(settings: Settings) -> WorkerRuntime:
    db = Database(settings)
    cache = CacheManager.from_settings(settings)
    concurrency = ConcurrencyManager(settings, cache)
    http = HttpFetcher(settings)
    browser = BrowserPool(settings)
    pipeline = ExtractionPipeline(settings, plugin_registry=build_extraction_plugins())
    extraction = ExtractionService(
        settings=settings,
        cache=cache,
        concurrency=concurrency,
        http_fetcher=http,
        browser_pool=browser,
        pipeline=pipeline,
    )
    registry = build_registry(
        settings,
        http_fetcher=http,
        browser_pool=browser,
        session_factory=db.session_factory,
    )
    orchestrator = SearchOrchestrator(
        settings=settings,
        registry=registry,
        cache=cache,
        concurrency=concurrency,
        ranking=RankingEngine(settings.ranking_weights),
        session_factory=db.session_factory,
        extraction_service=extraction,
    )
    jobs = JobService(settings=settings, session_factory=db.session_factory)
    return WorkerRuntime(
        settings=settings,
        db=db,
        cache=cache,
        concurrency=concurrency,
        http=http,
        browser=browser,
        registry=registry,
        pipeline=pipeline,
        extraction=extraction,
        orchestrator=orchestrator,
        jobs=jobs,
    )


def runtime_from_ctx(ctx: dict[Any, Any]) -> WorkerRuntime:
    runtime = ctx.get("runtime")
    if not isinstance(runtime, WorkerRuntime):
        raise RuntimeError("worker runtime is not initialised")
    return runtime

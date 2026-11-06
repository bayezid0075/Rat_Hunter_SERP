from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from app.adapters.search.registry import AdapterRegistry
from app.config import Settings
from app.core.cache import CacheManager, MemoryCache
from app.database.session import Database
from app.extractors.browser import BrowserPool
from app.main import create_app
from app.services.job_service import JobService

from tests.support import (
    PRODUCT_HTML,
    FakeArqPool,
    FakeSearchAdapter,
    Harness,
    StubHttpFetcher,
    create_api_key,
    sample_results,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        log_json=False,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        cache_backend="memory",
        enable_cache=True,
        cache_lock_timeout=2,
        api_key_pepper="test-pepper",
        google_enabled=False,
        direct_websites_enabled=False,
        custom_index_enabled=False,
        browser_fallback=False,
        default_rate_limit_per_minute=1_000,
        default_daily_quota=1_000,
        search_retries=0,
        quick_search_timeout=5.0,
        deep_search_timeout=8.0,
        adapter_timeout=2.0,
        extraction_timeout=3.0,
        global_request_deadline=10.0,
    )


@pytest_asyncio.fixture
async def harness(settings: Settings) -> AsyncIterator[Harness]:
    db = Database(settings)
    await db.create_all()
    cache = CacheManager(MemoryCache(), enabled=True, lock_timeout=2)

    adapter = FakeSearchAdapter()
    adapter.results = sample_results()
    registry = AdapterRegistry()
    registry.register(adapter)

    http = StubHttpFetcher(html=PRODUCT_HTML)
    browser_pool = BrowserPool(settings)
    arq = FakeArqPool()
    job_service = JobService(
        settings=settings,
        session_factory=db.session_factory,
        arq_pool=arq,
    )

    app = create_app(
        settings,
        overrides={
            "db": db,
            "cache": cache,
            "registry": registry,
            "http_fetcher": http,
            "browser_pool": browser_pool,
            "job_service": job_service,
        },
    )

    account, application, api_key, raw_key = await create_api_key(db, settings, name="primary")

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"X-API-Key": raw_key},
        ) as client:
            yield Harness(
                app=app,
                settings=settings,
                db=db,
                cache=cache,
                adapter=adapter,
                http=http,
                arq=arq,
                account_id=account.id,
                api_key_id=api_key.id,
                application_id=application.id if application else api_key.id,
                raw_key=raw_key,
                client=client,
            )


@pytest_asyncio.fixture
async def anon_client(harness: Harness) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=harness.app),
        base_url="http://testserver",
    ) as client:
        yield client

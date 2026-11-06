"""Opt-in integration tests against a real Redis instance.

Run with:
    SERP_TEST_REDIS_URL=redis://localhost:6379/15 python -m pytest tests/integration/test_redis_integration.py -q
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from app.config import Settings
from app.core.cache import CacheManager, RedisCache
from app.core.concurrency import CircuitBreaker, ConcurrencyManager
from app.core.exceptions import ConcurrencyTimeoutError
from app.core.rate_limit import RateLimiter

REDIS_URL = os.getenv("SERP_TEST_REDIS_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not REDIS_URL, reason="SERP_TEST_REDIS_URL is not configured"),
]


@pytest_asyncio.fixture
async def redis_cache() -> AsyncIterator[CacheManager]:
    assert REDIS_URL is not None
    settings = Settings(
        cache_backend="redis",
        redis_url=REDIS_URL,
        enable_cache=True,
        cache_lock_timeout=2,
        redis_max_connections=5,
    )
    cache = CacheManager.from_settings(settings)
    if not isinstance(cache.backend, RedisCache) or not await cache.ping():
        await cache.aclose()
        pytest.skip("configured Redis instance is not reachable")
    yield cache
    await cache.aclose()


@pytest.mark.asyncio
async def test_redis_cache_roundtrip_and_lock(redis_cache: CacheManager) -> None:
    assert isinstance(redis_cache.backend, RedisCache)
    client = redis_cache.backend.client
    token = uuid4().hex

    await redis_cache.set(f"test:cache:{token}", {"value": 1}, fresh_ttl=30, stale_ttl=60)
    entry = await redis_cache.get_entry(f"test:cache:{token}")
    assert entry is not None
    assert entry.value == {"value": 1}
    assert entry.cached is True
    assert entry.stale is False

    async with redis_cache.lock(f"test:lock:{token}"):
        assert await client.set(f"serp:lock:test:lock:{token}", "other", nx=True, ex=5) is None

    assert await client.get(f"serp:lock:test:lock:{token}") is None
    await client.delete(f"test:cache:{token}")


@pytest.mark.asyncio
async def test_redis_rate_limit_is_shared(redis_cache: CacheManager) -> None:
    assert REDIS_URL is not None
    assert isinstance(redis_cache.backend, RedisCache)
    settings = Settings(
        cache_backend="redis",
        redis_url=REDIS_URL,
        enable_cache=True,
        cache_lock_timeout=2,
        redis_max_connections=5,
    )
    limiter = RateLimiter.from_settings(settings, redis_cache)
    key = f"test:{uuid4().hex}"

    assert (await limiter.check(key, 2)).allowed is True
    assert (await limiter.check(key, 2)).allowed is True
    denied = await limiter.check(key, 2)
    assert denied.allowed is False
    assert denied.remaining == 0
    await redis_cache.backend.client.delete(f"serp:rate:{key}")


@pytest.mark.asyncio
async def test_redis_concurrency_and_circuit_breaker(redis_cache: CacheManager) -> None:
    assert REDIS_URL is not None
    assert isinstance(redis_cache.backend, RedisCache)
    client = redis_cache.backend.client
    settings = Settings(
        cache_backend="redis",
        redis_url=REDIS_URL,
        enable_cache=True,
        cache_lock_timeout=2,
        distributed_concurrency_ttl=5,
        redis_max_connections=5,
    )
    manager = ConcurrencyManager(settings, redis_cache)
    name = f"test:{uuid4().hex}"

    async with manager.acquire(name, limit=1, timeout=1):
        with pytest.raises(ConcurrencyTimeoutError):
            async with manager.acquire(name, limit=1, timeout=0.2):
                pytest.fail("second lease should not be acquired")
    assert await client.zcard(f"serp:sem:{name}") == 0

    breaker = CircuitBreaker(
        f"test:{uuid4().hex}",
        threshold=2,
        cooldown=10,
        redis_client=client,
    )
    assert await breaker.allow() is True
    await breaker.record_failure()
    assert await breaker.allow() is True
    await breaker.record_failure()
    assert await breaker.allow() is False
    await breaker.record_success()
    assert await breaker.allow() is True

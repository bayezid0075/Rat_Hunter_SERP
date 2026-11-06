from __future__ import annotations

import asyncio

import pytest
from app.config import Settings
from app.core.cache import CacheManager, MemoryCache
from app.core.concurrency import (
    CircuitBreaker,
    ConcurrencyManager,
    with_retry,
)
from app.core.exceptions import ConcurrencyTimeoutError


def _settings() -> Settings:
    return Settings(cache_backend="memory")


async def test_local_semaphore_limits_concurrency() -> None:
    manager = ConcurrencyManager(_settings(), CacheManager(MemoryCache()))
    active = 0
    peak = 0

    async def work() -> None:
        nonlocal active, peak
        async with manager.acquire("test", 2, timeout=1):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.05)
            active -= 1

    await asyncio.gather(*(work() for _ in range(6)))
    assert peak == 2


async def test_local_semaphore_timeout() -> None:
    manager = ConcurrencyManager(_settings(), CacheManager(MemoryCache()))
    async with manager.acquire("one", 1, timeout=1):
        with pytest.raises(ConcurrencyTimeoutError):
            async with manager.acquire("one", 1, timeout=0.05):
                pass


async def test_with_retry() -> None:
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return "ok"

    value, retried = await with_retry(
        flaky,
        retries=2,
        base_delay=0,
        is_retryable=lambda exc: isinstance(exc, RuntimeError),
    )
    assert value == "ok"
    assert retried is True
    assert attempts == 3


async def test_with_retry_stops_for_non_retryable() -> None:
    attempts = 0

    async def failing() -> str:
        nonlocal attempts
        attempts += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        await with_retry(
            failing,
            retries=3,
            base_delay=0,
            is_retryable=lambda exc: isinstance(exc, RuntimeError),
        )
    assert attempts == 1


async def test_circuit_breaker_opens_and_recovers() -> None:
    breaker = CircuitBreaker("source", threshold=2, cooldown=0.01)
    assert await breaker.allow()
    await breaker.record_failure()
    assert await breaker.allow()
    await breaker.record_failure()
    assert not await breaker.allow()
    await asyncio.sleep(0.02)
    assert await breaker.allow()
    await breaker.record_success()
    assert await breaker.allow()

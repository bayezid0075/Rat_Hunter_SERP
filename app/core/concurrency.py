"""Concurrency controls shared across API and worker processes.

Redis-backed leases are used whenever the configured cache backend is Redis.
Process-local semaphores are only used for the memory backend; they must never
be mistaken for a global system limit.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from redis.exceptions import RedisError

from app.config import Settings
from app.core.cache import CacheManager, RedisCache
from app.core.exceptions import ConcurrencyTimeoutError
from app.core.logging import get_logger

logger = get_logger(__name__)

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local expiry = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, expiry, member)
  redis.call('PEXPIRE', key, math.ceil((expiry - now) * 1000) + 1000)
  return 1
end
return 0
"""


class DistributedSemaphore:
    """A bounded lease semaphore implemented with a Redis sorted set.

    Each slot has a safety expiry so a crashed process cannot leak capacity
    forever.  The same interface is used for the process-local test backend.
    """

    def __init__(
        self,
        name: str,
        limit: int,
        *,
        redis_client: Any | None = None,
        ttl: int = 60,
    ) -> None:
        self.name = name
        self.limit = max(1, limit)
        self.ttl = ttl
        self._client = redis_client
        self._local = asyncio.Semaphore(self.limit)

    @asynccontextmanager
    async def acquire(self, timeout: float = 5.0) -> AsyncIterator[None]:
        if self._client is None:
            await self._acquire_local(timeout)
            try:
                yield
            finally:
                self._local.release()
            return

        member = uuid.uuid4().hex
        deadline = time.monotonic() + timeout
        acquired = False
        while time.monotonic() < deadline:
            now = time.time()
            try:
                acquired = bool(
                    await self._client.eval(
                        _ACQUIRE_SCRIPT,
                        1,
                        f"serp:sem:{self.name}",
                        now,
                        self.limit,
                        now + self.ttl,
                        member,
                    )
                )
            except (RedisError, ConnectionError, OSError) as exc:
                logger.warning("concurrency_backend_unavailable", exc_info=True)
                raise ConcurrencyTimeoutError() from exc
            if acquired:
                break
            await asyncio.sleep(0.05)
        if not acquired:
            raise ConcurrencyTimeoutError()
        try:
            yield
        finally:
            try:
                await self._client.zrem(f"serp:sem:{self.name}", member)
            except (RedisError, ConnectionError, OSError):
                logger.warning("concurrency_release_failed", exc_info=True)

    async def _acquire_local(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._local.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise ConcurrencyTimeoutError() from exc


class ConcurrencyManager:
    def __init__(self, settings: Settings, cache: CacheManager) -> None:
        self.settings = settings
        self._redis_client = (
            cache.backend.client
            if settings.cache_backend == "redis" and isinstance(cache.backend, RedisCache)
            else None
        )
        self._semaphores: dict[str, DistributedSemaphore] = {}

    @property
    def redis_client(self) -> Any | None:
        return self._redis_client

    def semaphore(self, name: str, limit: int) -> DistributedSemaphore:
        cache_key = f"{name}:{limit}"
        semaphore = self._semaphores.get(cache_key)
        if semaphore is None:
            semaphore = DistributedSemaphore(
                name,
                limit,
                redis_client=self._redis_client,
                ttl=self.settings.distributed_concurrency_ttl,
            )
            self._semaphores[cache_key] = semaphore
        return semaphore

    @asynccontextmanager
    async def acquire(self, name: str, limit: int, *, timeout: float = 5.0) -> AsyncIterator[None]:
        async with self.semaphore(name, limit).acquire(timeout):
            yield

    async def check_available(self) -> bool:
        if self._redis_client is None:
            return True
        try:
            return bool(await self._redis_client.ping())
        except (RedisError, ConnectionError, OSError):
            return False


class CircuitBreaker:
    """Per-source circuit breaker with Redis-backed shared state."""

    def __init__(
        self,
        name: str,
        *,
        threshold: int = 5,
        cooldown: int = 30,
        redis_client: Any | None = None,
    ) -> None:
        self.name = name
        self.threshold = threshold
        self.cooldown = cooldown
        self._client = redis_client
        self._failures = 0
        self._open_until = 0.0

    def _keys(self) -> tuple[str, str]:
        return f"serp:cb:{self.name}:failures", f"serp:cb:{self.name}:open"

    async def allow(self) -> bool:
        if self._client is None:
            return time.monotonic() >= self._open_until
        _, open_key = self._keys()
        try:
            return not bool(await self._client.exists(open_key))
        except (RedisError, ConnectionError, OSError):
            logger.warning("circuit_breaker_backend_unavailable", exc_info=True)
            return True

    async def record_success(self) -> None:
        if self._client is None:
            self._failures = 0
            self._open_until = 0.0
            return
        failures_key, open_key = self._keys()
        try:
            await self._client.delete(failures_key, open_key)
        except (RedisError, ConnectionError, OSError):
            logger.warning("circuit_breaker_state_failed", exc_info=True)

    async def record_failure(self) -> None:
        if self._client is None:
            self._failures += 1
            if self._failures >= self.threshold:
                self._open_until = time.monotonic() + self.cooldown
                self._failures = 0
            return
        failures_key, open_key = self._keys()
        try:
            failures = await self._client.incr(failures_key)
            await self._client.expire(failures_key, self.cooldown * 2)
            if int(failures) >= self.threshold:
                await self._client.set(open_key, "1", ex=self.cooldown)
                await self._client.delete(failures_key)
        except (RedisError, ConnectionError, OSError):
            logger.warning("circuit_breaker_state_failed", exc_info=True)


async def with_retry[T](
    factory: Callable[[], Awaitable[T]],
    *,
    retries: int,
    base_delay: float = 0.2,
    is_retryable: Callable[[Exception], bool],
) -> tuple[T, bool]:
    """Run ``factory`` with bounded retries. Returns (value, was_retried)."""

    attempt = 0
    retried = False
    while True:
        try:
            return await factory(), retried
        except Exception as exc:
            if attempt >= retries or not is_retryable(exc):
                raise
            attempt += 1
            retried = True
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

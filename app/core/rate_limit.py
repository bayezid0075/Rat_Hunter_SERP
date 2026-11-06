"""Distributed sliding-window rate limiting.

When Redis is configured, limits are shared by every API process.  The memory
backend is intentionally only used for tests or explicitly configured
single-process development, because process-local limits are not a global
limit.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any

from redis.exceptions import RedisError

from app.config import Settings
from app.core.cache import CacheManager, RedisCache
from app.core.exceptions import RedisUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window + 1000)
  return {1, count + 1}
end
redis.call('PEXPIRE', key, window + 1000)
return {0, count}
"""


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    used: int
    limit: int
    remaining: int
    reset_after_seconds: float


class RateLimiter:
    def __init__(self, redis_client: Any | None = None, *, window_seconds: int = 60) -> None:
        self._client = redis_client
        self._window = window_seconds
        self._memory: dict[str, deque[float]] = defaultdict(deque)
        self._guard = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings, cache: CacheManager) -> RateLimiter:
        if settings.cache_backend == "redis" and isinstance(cache.backend, RedisCache):
            return cls(cache.backend.client)
        return cls(None)

    async def check(self, key: str, limit: int, *, window_seconds: int | None = None) -> RateLimitResult:
        limit = max(1, limit)
        window = window_seconds or self._window
        if self._client is not None:
            return await self._check_redis(key, limit, window)
        return await self._check_memory(key, limit, window)

    async def _check_redis(self, key: str, limit: int, window: int) -> RateLimitResult:
        assert self._client is not None
        now_ms = int(time.time() * 1000)
        window_ms = window * 1000
        member = f"{now_ms}-{uuid.uuid4().hex}"
        try:
            result = await self._client.eval(
                _SLIDING_WINDOW_SCRIPT, 1, f"serp:rate:{key}", now_ms, window_ms, limit, member
            )
        except (RedisError, ConnectionError, OSError) as exc:
            logger.error("rate_limit_backend_unavailable", exc_info=True)
            raise RedisUnavailableError("Rate limiting is temporarily unavailable") from exc
        allowed = bool(int(result[0]))
        used = int(result[1])
        return RateLimitResult(
            allowed=allowed,
            used=used,
            limit=limit,
            remaining=max(0, limit - used),
            reset_after_seconds=float(window),
        )

    async def _check_memory(self, key: str, limit: int, window: int) -> RateLimitResult:
        now = time.monotonic()
        async with self._guard:
            bucket = self._memory[key]
            cutoff = now - window
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) < limit:
                bucket.append(now)
                used = len(bucket)
                allowed = True
            else:
                used = len(bucket)
                allowed = False
            reset_after = max(0.0, window - (now - bucket[0])) if bucket else float(window)
        return RateLimitResult(
            allowed=allowed,
            used=used,
            limit=limit,
            remaining=max(0, limit - used),
            reset_after_seconds=reset_after,
        )

"""Redis-backed caching with an in-memory backend for tests.

Features: cache-aside, stale-while-revalidate, cache bypass, invalidation,
stampede prevention and request coalescing.  Keys hash a canonical tuple that
includes the normalized query, country, language, search type, limit, page and
relevant source configuration.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

import orjson
import redis.asyncio as redis
from redis.exceptions import RedisError

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


def canonical_digest(*parts: Any) -> str:
    payload = orjson.dumps(parts, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(payload).hexdigest()


def search_cache_key(
    *,
    query: str,
    country: str,
    language: str,
    search_type: str,
    limit: int,
    page: int,
    sources: list[str] | None,
    mode: str,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    safe_search: bool = True,
    extract_limit: int = 0,
) -> str:
    digest = canonical_digest(
        "search",
        query.casefold().strip(),
        country.upper(),
        language.lower(),
        search_type,
        limit,
        page,
        sorted(sources or []),
        sorted(include_domains or []),
        sorted(exclude_domains or []),
        safe_search,
        extract_limit,
        mode,
    )
    return f"serp:search:{digest}"


def extraction_cache_key(url: str, *, include_text: bool, include_media: bool) -> str:
    digest = canonical_digest("extract", url, include_text, include_media)
    return f"serp:extract:{digest}"


def source_health_key(name: str) -> str:
    return f"serp:source-health:{name}"


class CacheBackend(Protocol):
    async def get(self, key: str) -> bytes | None: ...
    async def set(self, key: str, value: bytes, ttl: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def ping(self) -> bool: ...
    async def aclose(self) -> None: ...


class MemoryCache:
    """Process-local cache used only for tests and single-process development."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, bytes]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> bytes | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        async with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._data.pop(key, None)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self._data.clear()


class RedisCache:
    def __init__(self, url: str, *, max_connections: int = 50) -> None:
        self._client: Any = redis.from_url(  # type: ignore[no-untyped-call]
            url,
            max_connections=max_connections,
            decode_responses=False,
            socket_connect_timeout=3,
            socket_timeout=3,
            retry_on_timeout=False,
        )

    @property
    def client(self) -> Any:
        return self._client

    async def get(self, key: str) -> bytes | None:
        return cast("bytes | None", await self._client.get(key))

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        await self._client.set(key, value, ex=max(1, ttl))

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except RedisError:
            logger.warning("redis_ping_failed", exc_info=True)
            return False

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(slots=True)
class CacheEntry:
    value: Any
    cached: bool
    stale: bool


class CacheManager:
    """High-level cache façade; cache failures degrade to a cache miss."""

    def __init__(
        self,
        backend: CacheBackend,
        *,
        enabled: bool = True,
        lock_timeout: float = 10.0,
    ) -> None:
        self.backend = backend
        self.enabled = enabled
        self.lock_timeout = lock_timeout
        self._local_locks: dict[str, asyncio.Lock] = {}
        self._local_locks_guard = asyncio.Lock()
        self._refreshing: set[str] = set()

    @classmethod
    def from_settings(cls, settings: Settings) -> CacheManager:
        if settings.cache_backend == "memory":
            backend: CacheBackend = MemoryCache()
        else:
            backend = RedisCache(settings.redis_url, max_connections=settings.redis_max_connections)
        return cls(
            backend,
            enabled=settings.enable_cache,
            lock_timeout=settings.cache_lock_timeout,
        )

    async def _get_raw(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        try:
            raw = await self.backend.get(key)
        except (RedisError, ConnectionError, OSError):
            logger.warning("cache_read_failed", extra={"cache_key": key}, exc_info=True)
            return None
        if raw is None:
            return None
        try:
            parsed = orjson.loads(raw)
        except orjson.JSONDecodeError:
            logger.warning("cache_value_corrupt", extra={"cache_key": key})
            return None
        if not isinstance(parsed, dict) or "v" not in parsed or "fresh_until" not in parsed:
            return None
        return parsed

    async def _set_raw(self, key: str, value: Any, *, fresh_ttl: int, stale_ttl: int) -> None:
        if not self.enabled:
            return
        now = time.time()
        payload = orjson.dumps({"v": value, "stored_at": now, "fresh_until": now + max(1, fresh_ttl)})
        try:
            await self.backend.set(key, payload, max(fresh_ttl, stale_ttl, 1))
        except (RedisError, ConnectionError, OSError):
            logger.warning("cache_write_failed", extra={"cache_key": key}, exc_info=True)

    async def delete(self, key: str) -> None:
        try:
            await self.backend.delete(key)
        except (RedisError, ConnectionError, OSError):
            logger.warning("cache_delete_failed", extra={"cache_key": key}, exc_info=True)

    async def get_entry(self, key: str) -> CacheEntry | None:
        raw = await self._get_raw(key)
        if raw is None:
            return None
        return CacheEntry(value=raw["v"], cached=True, stale=time.time() > float(raw["fresh_until"]))

    async def set(self, key: str, value: Any, *, fresh_ttl: int, stale_ttl: int | None = None) -> None:
        await self._set_raw(
            key,
            value,
            fresh_ttl=fresh_ttl,
            stale_ttl=stale_ttl if stale_ttl is not None else fresh_ttl,
        )

    @asynccontextmanager
    async def lock(self, key: str, *, timeout: float | None = None) -> AsyncIterator[None]:
        """Cross-process mutex used to coalesce identical requests."""

        lock_key = f"serp:lock:{key}"
        token = uuid.uuid4().hex
        deadline = time.monotonic() + (timeout or self.lock_timeout)
        acquired = False
        client = getattr(self.backend, "client", None)
        if client is not None:
            while time.monotonic() < deadline:
                try:
                    acquired = bool(await client.set(lock_key, token, nx=True, ex=int(self.lock_timeout) + 1))
                except (RedisError, ConnectionError, OSError):
                    logger.warning("cache_lock_backend_failed", exc_info=True)
                    break
                if acquired:
                    break
                await asyncio.sleep(0.05)
        if not acquired:
            local_lock = await self._get_local_lock(lock_key)
            try:
                await asyncio.wait_for(local_lock.acquire(), timeout=max(0.1, deadline - time.monotonic()))
                acquired = True
                try:
                    yield
                finally:
                    local_lock.release()
                return
            except TimeoutError:
                yield
                return
        try:
            yield
        finally:
            if client is not None:
                try:
                    await client.eval(_RELEASE_LOCK_SCRIPT, 1, lock_key, token)
                except (RedisError, ConnectionError, OSError):
                    logger.warning("cache_unlock_backend_failed", exc_info=True)

    async def _get_local_lock(self, key: str) -> asyncio.Lock:
        async with self._local_locks_guard:
            lock = self._local_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._local_locks[key] = lock
            return lock

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        fresh_ttl: int,
        stale_ttl: int | None = None,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> CacheEntry:
        if not self.enabled or not use_cache:
            return CacheEntry(await factory(), cached=False, stale=False)

        entry = await self.get_entry(key)
        if entry is not None and not force_refresh:
            if not entry.stale:
                return entry
            self._schedule_refresh(key, factory, fresh_ttl, stale_ttl)
            return CacheEntry(entry.value, cached=True, stale=True)

        async with self.lock(key):
            entry = await self.get_entry(key)
            if entry is not None and not entry.stale and not force_refresh:
                return entry
            value = await factory()
            await self.set(key, value, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl)
            return CacheEntry(value, cached=False, stale=False)

    def _schedule_refresh(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        fresh_ttl: int,
        stale_ttl: int | None,
    ) -> None:
        if key in self._refreshing:
            return
        self._refreshing.add(key)

        async def _refresh() -> None:
            try:
                value = await factory()
                await self.set(key, value, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl)
            except Exception:
                logger.warning("stale_refresh_failed", extra={"cache_key": key}, exc_info=True)
            finally:
                self._refreshing.discard(key)

        task = asyncio.create_task(_refresh(), name=f"cache-refresh:{key[:24]}")
        task.add_done_callback(lambda _: None)

    async def ping(self) -> bool:
        return await self.backend.ping()

    async def aclose(self) -> None:
        await self.backend.aclose()

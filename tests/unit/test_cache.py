from __future__ import annotations

import asyncio
from typing import Any

from app.core.cache import (
    CacheManager,
    MemoryCache,
    canonical_digest,
    extraction_cache_key,
    search_cache_key,
)


class FailingBackend:
    async def get(self, key: str) -> bytes | None:
        raise OSError("cache down")

    async def set(self, key: str, value: bytes, ttl: int) -> None:
        raise OSError("cache down")

    async def delete(self, key: str) -> None:
        raise OSError("cache down")

    async def ping(self) -> bool:
        return False

    async def aclose(self) -> None:
        return None


def test_digest_and_keys_are_stable() -> None:
    assert canonical_digest(["a", 1]) == canonical_digest(["a", 1])
    assert canonical_digest(["a", 1]) != canonical_digest(["a", 2])

    key = search_cache_key(
        query="Laptop",
        country="BD",
        language="en",
        search_type="web",
        limit=10,
        page=1,
        sources=["daraz", "google"],
        mode="quick",
    )
    same = search_cache_key(
        query=" laptop ",
        country="bd",
        language="EN",
        search_type="web",
        limit=10,
        page=1,
        sources=["google", "daraz"],
        mode="quick",
    )
    assert key == same
    assert (
        search_cache_key(
            query="Laptop",
            country="BD",
            language="en",
            search_type="web",
            limit=10,
            page=2,
            sources=["daraz", "google"],
            mode="quick",
        )
        != key
    )
    assert extraction_cache_key("https://example.com/a", include_text=True, include_media=False).startswith(
        "serp:extract:"
    )


async def test_get_or_set_and_bypass() -> None:
    manager = CacheManager(MemoryCache())
    calls = 0

    async def factory() -> dict[str, int]:
        nonlocal calls
        calls += 1
        return {"value": calls}

    first = await manager.get_or_set("key", factory, fresh_ttl=60)
    second = await manager.get_or_set("key", factory, fresh_ttl=60)
    bypass = await manager.get_or_set("key", factory, fresh_ttl=60, use_cache=False)
    assert first.value == {"value": 1}
    assert first.cached is False
    assert second.value == {"value": 1}
    assert second.cached is True
    assert bypass.value == {"value": 2}
    assert bypass.cached is False
    assert calls == 2


async def test_force_refresh_replaces_value() -> None:
    manager = CacheManager(MemoryCache())
    values = iter([1, 2])

    async def factory() -> int:
        return next(values)

    assert (await manager.get_or_set("force", factory, fresh_ttl=60)).value == 1
    refreshed = await manager.get_or_set("force", factory, fresh_ttl=60, force_refresh=True)
    assert refreshed.value == 2
    assert refreshed.cached is False


async def test_concurrent_requests_coalesce() -> None:
    manager = CacheManager(MemoryCache(), lock_timeout=2)
    calls = 0

    async def factory() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.1)
        return {"value": calls}

    results = await asyncio.gather(*(manager.get_or_set("same", factory, fresh_ttl=60) for _ in range(5)))
    assert calls == 1
    assert {result.value["value"] for result in results} == {1}


async def test_cache_failures_degrade_to_miss() -> None:
    manager = CacheManager(FailingBackend())
    calls = 0

    async def factory() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    first = await manager.get_or_set("down", factory, fresh_ttl=60)
    second = await manager.get_or_set("down", factory, fresh_ttl=60)
    assert first.value == {"ok": True}
    assert second.value == {"ok": True}
    assert calls == 2
    assert await manager.ping() is False

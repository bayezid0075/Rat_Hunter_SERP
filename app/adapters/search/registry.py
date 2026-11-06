"""Adapter registry and dependency wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.adapters.search.base import SearchAdapter
from app.config import Settings
from app.core.logging import get_logger
from app.schemas.common import SearchType

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.extractors.browser import BrowserPool
    from app.extractors.http import HttpFetcher

logger = get_logger(__name__)


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SearchAdapter] = {}

    def register(self, adapter: SearchAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"adapter already registered: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def unregister(self, name: str) -> None:
        self._adapters.pop(name, None)

    def get(self, name: str) -> SearchAdapter | None:
        return self._adapters.get(name)

    def all(self) -> list[SearchAdapter]:
        return list(self._adapters.values())

    def names(self) -> list[str]:
        return list(self._adapters)

    def select(
        self,
        *,
        requested: list[str] | None,
        search_type: SearchType,
    ) -> list[SearchAdapter]:
        adapters = [adapter for adapter in self._adapters.values() if adapter.supports(search_type)]
        if requested:
            requested_set = {name.lower() for name in requested}
            adapters = [
                adapter
                for adapter in adapters
                if adapter.name.lower() in requested_set or adapter.adapter_type.lower() in requested_set
            ]
        return adapters

    async def close(self) -> None:
        for adapter in self._adapters.values():
            try:
                await adapter.close()
            except Exception:
                logger.warning("adapter_close_failed", extra={"adapter": adapter.name}, exc_info=True)


def build_registry(
    settings: Settings,
    *,
    http_fetcher: HttpFetcher,
    browser_pool: BrowserPool,
    session_factory: async_sessionmaker[AsyncSession],
) -> AdapterRegistry:
    """Construct enabled adapters without coupling the orchestrator to them."""

    from app.adapters.search.custom_index import CustomIndexAdapter
    from app.adapters.search.direct import DirectWebsiteAdapter
    from app.adapters.search.google import GoogleBrowserAdapter
    from app.adapters.search.plugins import build_plugins

    registry = AdapterRegistry()
    if settings.google_enabled:
        registry.register(GoogleBrowserAdapter(settings=settings, browser_pool=browser_pool))
    if settings.direct_websites_enabled:
        for plugin in build_plugins(settings):
            registry.register(DirectWebsiteAdapter(plugin=plugin, settings=settings, http_fetcher=http_fetcher))
    if settings.custom_index_enabled:
        registry.register(CustomIndexAdapter(settings=settings, session_factory=session_factory))
    if not registry.names():
        logger.warning("no_search_adapters_enabled")
    return registry

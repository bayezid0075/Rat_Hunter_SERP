"""Adapter wrapper around a direct website search plugin."""

from __future__ import annotations

import time

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.adapters.search.plugins.base import WebsiteSearchPlugin
from app.config import Settings
from app.extractors.http import HttpFetcher
from app.schemas.common import SearchType
from app.schemas.search import SearchResult


class DirectWebsiteAdapter(SearchAdapter):
    adapter_type = "direct_website"

    def __init__(
        self,
        *,
        plugin: WebsiteSearchPlugin,
        settings: Settings,
        http_fetcher: HttpFetcher,
    ) -> None:
        self.plugin = plugin
        self.settings = settings
        self.http_fetcher = http_fetcher
        self.name = f"direct_{plugin.name}"
        self.description = f"Direct search integration for {plugin.display_name}"
        self.capabilities = (SearchType.WEB, SearchType.PRODUCTS)
        self.requires_browser = plugin.requires_js
        self.concurrency_limit = settings.per_adapter_concurrency

    async def search(self, options: SearchOptions) -> list[SearchResult]:
        bounded = SearchOptions(
            query=options.query,
            country=options.country,
            language=options.language,
            search_type=options.search_type,
            limit=options.limit,
            page=options.page,
            safe_search=options.safe_search,
            include_domains=options.include_domains,
            exclude_domains=options.exclude_domains,
            timeout_seconds=min(options.timeout_seconds, self.settings.direct_website_timeout),
            extra=options.extra,
        )
        html, final_url = await self.plugin.fetch_search(bounded, self.http_fetcher)
        return self.plugin.parse(
            html,
            final_url,
            limit=bounded.limit,
            adapter_name=self.name,
        )[: bounded.limit]

    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        health = await self.plugin.health_check(self.http_fetcher)
        return AdapterHealth(
            name=self.name,
            status=health.status,
            latency_ms=health.latency_ms if health.latency_ms is not None else (time.perf_counter() - started) * 1000,
            error=health.error,
        )

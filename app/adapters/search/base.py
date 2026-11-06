"""Formal search adapter contract.

Adapters are intentionally isolated from the orchestrator.  A new source is
added by implementing this interface and registering a factory; routes, core
schemas, extraction and persistence do not need to change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.schemas.common import SearchType, SourceHealthStatus
from app.schemas.search import SearchResult


@dataclass(slots=True)
class SearchOptions:
    query: str
    country: str = "US"
    language: str = "en"
    search_type: SearchType = SearchType.WEB
    limit: int = 10
    page: int = 1
    safe_search: bool = True
    include_domains: list[str] = field(default_factory=list)
    exclude_domains: list[str] = field(default_factory=list)
    timeout_seconds: float = 6.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AdapterHealth:
    name: str
    status: SourceHealthStatus
    latency_ms: float | None = None
    error: str | None = None


class SearchAdapter(ABC):
    """Base class for every search source."""

    name: str
    adapter_type: str = "unknown"
    capabilities: tuple[SearchType, ...] = (SearchType.WEB,)
    requires_browser: bool = False
    description: str = ""
    concurrency_limit: int = 5

    def supports(self, search_type: SearchType) -> bool:
        return search_type in self.capabilities

    @abstractmethod
    async def search(self, options: SearchOptions) -> list[SearchResult]:
        """Return standardized results or raise an ``AppError`` subclass."""

    @abstractmethod
    async def health_check(self) -> AdapterHealth:
        """Return a bounded, non-destructive health status."""

    async def close(self) -> None:  # noqa: B027 - optional no-op default
        """Release adapter-owned resources (optional)."""

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "adapter_type": self.adapter_type,
            "enabled": True,
            "capabilities": [str(item) for item in self.capabilities],
            "concurrency_limit": self.concurrency_limit,
            "requires_browser": self.requires_browser,
            "description": self.description or None,
        }

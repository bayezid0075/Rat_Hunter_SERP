"""Search adapters and the adapter registry."""

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.adapters.search.registry import AdapterRegistry, build_registry

__all__ = ["AdapterHealth", "AdapterRegistry", "SearchAdapter", "SearchOptions", "build_registry"]

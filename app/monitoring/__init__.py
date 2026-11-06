"""Prometheus metrics and optional Sentry integration."""

from app.monitoring.metrics import (
    ACTIVE_BROWSER_PAGES,
    ADAPTER_FAILURES,
    ADAPTER_SUCCESS,
    CACHE_HITS,
    CACHE_MISSES,
    EXTRACTION_LATENCY,
    QUEUE_DEPTH,
    SEARCH_LATENCY,
    metrics_response,
)

__all__ = [
    "ACTIVE_BROWSER_PAGES",
    "ADAPTER_FAILURES",
    "ADAPTER_SUCCESS",
    "CACHE_HITS",
    "CACHE_MISSES",
    "EXTRACTION_LATENCY",
    "QUEUE_DEPTH",
    "SEARCH_LATENCY",
    "metrics_response",
]

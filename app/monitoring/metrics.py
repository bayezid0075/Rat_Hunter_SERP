"""Prometheus-compatible metrics."""

from __future__ import annotations

from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUEST_COUNT = Counter("serp_api_requests_total", "API requests", ["endpoint", "method", "status"])
REQUEST_LATENCY = Histogram(
    "serp_api_request_duration_seconds",
    "API request latency",
    ["endpoint", "method"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 20, 30),
)
SEARCH_LATENCY = Histogram(
    "serp_search_duration_seconds",
    "Search orchestration latency",
    ["mode", "search_type", "cached"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 3, 5, 8, 15, 30),
)
EXTRACTION_LATENCY = Histogram(
    "serp_extraction_duration_seconds",
    "Extraction latency",
    ["browser_used"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1, 2, 3, 5, 8, 15, 30),
)
ADAPTER_SUCCESS = Counter("serp_adapter_success_total", "Successful adapter calls", ["source"])
ADAPTER_FAILURES = Counter("serp_adapter_failures_total", "Failed adapter calls", ["source", "error_code"])
CACHE_HITS = Counter("serp_cache_hits_total", "Cache hits", ["category"])
CACHE_MISSES = Counter("serp_cache_misses_total", "Cache misses", ["category"])
QUEUE_DEPTH = Gauge("serp_queue_depth", "Background queue depth", ["queue"])
ACTIVE_BROWSER_PAGES = Gauge("serp_active_browser_pages", "Currently active Playwright pages")
BROWSER_INSTANCES = Gauge("serp_browser_instances", "Running browser instances")
DB_HEALTH = Gauge("serp_database_up", "Database readiness (1/0)")
REDIS_HEALTH = Gauge("serp_redis_up", "Redis readiness (1/0)")


def observe_request(endpoint: str, method: str, status: int, duration: float) -> None:
    REQUEST_COUNT.labels(endpoint, method, str(status)).inc()
    REQUEST_LATENCY.labels(endpoint, method).observe(duration)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

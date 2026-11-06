# Testing

## Goals

The default suite must be deterministic, fast, and independent of live Google or arbitrary external websites. It should catch regressions in parsing, security boundaries, API contracts, caching, ranking, and job orchestration without requiring PostgreSQL, Redis, Playwright, or network access.

## Test Layout

```text
tests/
  support.py            fakes, harness, API key helpers, HTML fixtures
  conftest.py           SQLite app, memory cache, lifecycle, HTTP client
  unit/
    test_adapters.py
    test_cache.py
    test_concurrency.py
    test_browser_pool.py
    test_extractors.py
    test_processors.py
    test_security.py
  integration/
    test_search_api.py
    test_extraction_api.py
    test_account_sources_health.py
    test_worker_jobs.py
    test_redis_integration.py   (opt-in)
  security/
    test_ssrf.py
  performance/
    k6_load_test.js
```

## Run

All tests:

```bash
python -m pytest -q
```

By category:

```bash
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest tests/security -q
```

Real Redis integration tests are opt-in and use a dedicated database:

```bash
SERP_TEST_REDIS_URL=redis://localhost:6379/15 \
  python -m pytest tests/integration/test_redis_integration.py -q
```

The normal suite skips these when `SERP_TEST_REDIS_URL` is unset. Use a disposable Redis database; the tests delete the keys they create.

Verbose with short tracebacks:

```bash
python -m pytest -vv --tb=short
```

Stop on first failure:

```bash
python -m pytest -x
```

Markers are strict. Registered markers are `integration`, `security`, `performance`, and `slow`. Use a marker only if the test actually matches it.

## Coverage

```bash
python -m pytest --cov=app --cov-report=term-missing
python -m pytest --cov=app --cov-report=html
```

Coverage configuration is in `pyproject.toml`. Workers and migrations are intentionally omitted from the default coverage source because they require external infrastructure.

## Test Harness

`tests/support.py` provides:

- `Harness`: creates accounts, API keys, and authenticated clients.
- `create_api_key(...)`: inserts a hashed key and returns the raw key.
- `FakeSearchAdapter`: deterministic adapter results and failures.
- `StubHttpFetcher`: deterministic HTTP responses without network access.
- `FakeArqPool`: records enqueued jobs without Redis.
- HTML fixtures for product, article, Google, and Daraz pages.

`tests/conftest.py` builds an app with overrides:

- temporary SQLite database
- memory cache
- fake adapters
- stub HTTP fetcher
- fake ARQ pool
- lifespan-managed `httpx.AsyncClient`

No external network is used by the default suite.

## What Is Covered

### Unit

- query normalization, URL normalization, domain filters, content classification
- price parsing, currency detection, deduplication, ranking
- cache keys, cache-aside, bypass, force refresh, coalescing, failure degradation
- local semaphore, timeouts, retries, non-retryable retries, circuit breaker
- JSON-LD, metadata, microdata/RDFa-adjacent behavior, HTML heuristics
- nested/unhashable product merge regression
- Google URL unwrapping and block detection
- direct website plugin parsing
- API key hashing, pepper behavior, URL safety, DNS validation
- SSRF redirect blocking, metadata IP blocking, response limit, content type

### Integration

- search responses, ranking, cache hits and bypass, domain filters
- unknown/all-failing sources
- deep-search extraction
- product price filtering
- API key missing/invalid/revoked/expired cases
- rate limit and daily quota
- request ID sanitization and security headers
- single/batch extraction, cache behavior, SSRF rejection
- async search/extraction/browser job creation
- search, extraction, and forced-browser task execution against fakes
- job success/failure persistence and attempt tracking
- browser pool start, page acquisition, cleanup, restart, and SSRF route guard using a fake Playwright
- account, usage, health, readiness, metrics, sources, source health
- missing job behavior
- real Redis cache TTL, distributed lock, shared rate limit, distributed semaphore, and circuit breaker when opt-in

### Security

- private-address redirects
- link-local metadata redirects
- oversized responses
- non-HTML extraction rejection
- credentials and unusual ports

### Not Covered by the Default Suite

- live Google access
- live direct-website availability
- real PostgreSQL integration
- a running ARQ worker process connected to Redis
- real Playwright browser rendering
- a 30-minute k6 load test

Real Redis behavior is covered by the opt-in tests above. The remaining items require external infrastructure and are documented in [PERFORMANCE.md](PERFORMANCE.md). Do not describe them as verified unless they were actually run in the target environment.

## Adding Tests

### Parser regression

Place a minimal HTML fixture in `tests/support.py` or inline in the test. Assert normalized fields and provenance, not internal parser details.

### API behavior

Use the `harness` fixture and the authenticated client. Example:

```python
async def test_new_endpoint(harness: Harness) -> None:
    response = await harness.client.post("/v1/new", json={"value": "ok"})
    assert response.status_code == 200
    assert response.json()["success"] is True
```

For anonymous endpoints, use the `anon_client` fixture.

### Adapter failure

Use `FakeSearchAdapter` with a failure mode or a custom fake that raises `SourceTimeoutError` or `SourceUnavailableError`. Assert partial results and source statuses.

### Cache behavior

Use `MemoryCache` and `CacheManager` directly. Assert factory call counts, `cached`/`stale` flags, and coalescing.

### Security regression

Add a test that proves the unsafe target is rejected and that no outbound request is made. Use `StubHttpFetcher` or the `_public_resolver` pattern in `tests/security/test_ssrf.py`.

### Async jobs

Use `FakeArqPool` to assert function name and queue selection. Do not require Redis. If testing task execution, provide a fake runtime with a job service and extraction/orchestrator stubs rather than connecting to a real queue.

## Static Verification

Run all quality gates:

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy app
python -m compileall -q app migrations tests scripts
python -c "from app.main import app; print(len(app.routes))"
```

The last command is a lightweight import smoke test. FastAPI uses lazy included-router objects in the installed version; a small route count is expected and is not an endpoint-count assertion.

## Failure Triage

1. Read the first failure, not only the final summary.
2. Re-run the single test with `-vv`.
3. Check whether a fixture assumption changed.
4. For extraction failures, inspect the fixture HTML and structured-data path.
5. For merge failures, verify nested values are handled without hashing assumptions.
6. For API failures, inspect `response.json()` and the request ID.
7. For environment failures, check `.env`, SQLite path, Redis/PostgreSQL availability, and worker imports.
8. Do not weaken the test to make a real bug pass.

## Performance Tests

The k6 script is not run by pytest. See [PERFORMANCE.md](PERFORMANCE.md) for commands, workloads, metrics, and reporting.

# Architecture

## Design Goals

- Keep discovery, extraction, persistence, and provider-specific logic separate.
- Make every operational limit configurable through the environment.
- Prefer bounded work and explicit provenance over fabricated or silently merged data.
- Treat upstream failures as partial results when possible and structured errors otherwise.
- Keep the automated suite independent of live Google and arbitrary external websites.

## Runtime Components

```text
                         +----------------------+
HTTP clients ----------->| FastAPI API process  |
                         | auth, routes, cache  |
                         +----------+-----------+
                                    |
              +---------------------+---------------------+
              |                     |                     |
      +-------v-------+     +-------v-------+     +-------v--------+
      | PostgreSQL    |     | Redis         |     | ARQ workers    |
      | history, jobs |     | cache, locks  |     | search/extract |
      | usage, index  |     | rate limits   |     | browser        |
      +---------------+     +---------------+     +----------------+
                                                          |
                                                  +-------v-------+
                                                  | Playwright    |
                                                  | Chromium pool |
                                                  +---------------+
```

The API process is a modular monolith. Workers reuse the same settings, database, cache, extraction pipeline, registry, and orchestrator construction through `app.workers.runtime.WorkerRuntime`.

## Application Startup

`app.main.lifespan` constructs or reuses these resources:

- `Database`
- `CacheManager`
- `HttpFetcher`
- `BrowserPool`
- `ConcurrencyManager`
- `RateLimiter`
- `ExtractionPipeline` and extraction plugins
- `ExtractionService`
- `AdapterRegistry`
- `SearchOrchestrator`
- `AuthService`, `UsageService`, and `JobService`

Tests can inject replacements through `create_app(overrides={...})`. Startup pings the database and cache but only logs warnings if they are unavailable; `/v1/ready` reports readiness. Shutdown closes adapters, the browser pool, HTTP client, cache, and database in reverse dependency order.

## Middleware

Middleware is added in this order:

1. `AccessLogMiddleware`: request timing, Prometheus observation, usage recording, security headers, structured request logs.
2. `BodySizeLimitMiddleware`: rejects declared bodies above `MAX_REQUEST_BODY_BYTES` with `413`.
3. `RequestContextMiddleware`: accepts or generates `X-Request-ID`, stores it in context, and echoes it on the response.
4. Optional CORS and `TrustedHost` middleware.

Security headers set by default:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`
- `Cache-Control: no-store`

## Authentication and Limits

`AuthService` hashes the presented key with HMAC-SHA256 and `API_KEY_PEPPER`, then looks up the hash in PostgreSQL. The safe snapshot is cached in Redis or memory for `API_KEY_CACHE_TTL`.

The resolved snapshot includes key, account, and application state. Revoked keys, expired keys, inactive accounts, and inactive applications are rejected. Effective rate and quota limits use key overrides first, then account overrides, then defaults.

Rate limiting uses a Redis sorted-set sliding window when `CACHE_BACKEND=redis`. The memory limiter exists for tests and explicitly single-process development only.

## Search Flow

`POST /v1/search` and `POST /v1/products/search` call `SearchOrchestrator.search`:

1. Normalize query whitespace and build a canonical cache key from query, locale, type, mode, page, limit, source selection, domain filters, safe search, and deep-extraction count.
2. Use cache-aside with fresh/stale TTLs and single-flight refresh.
3. Select adapters by requested source name/type and support for the requested `SearchType`.
4. Acquire global search concurrency, then call adapters concurrently under the request deadline.
5. Each adapter has its own concurrency limit, timeout, retry policy, and circuit breaker.
6. Normalize URLs, apply include/exclude domain filters, deduplicate, and rank.
7. In deep mode, extract a bounded number of top results.
8. Return partial results plus per-source statuses when some adapters fail.
9. Persist search history after the response is built; write failures are logged and do not discard the results.

The ranking engine uses weighted position, title relevance, query similarity, domain relevance, content type, source reliability, and freshness. Weights can be overridden with `RANKING_WEIGHTS_JSON`.

## Search Adapters

All adapters implement `SearchAdapter`:

- `search(SearchOptions) -> list[SearchResult]`
- `health_check() -> AdapterHealth`
- optional `close()`

Built-in adapters:

- `GoogleBrowserAdapter`: renders Google through the Playwright pool. It supports web, products, and news. CAPTCHA/access-denied pages become explicit errors; no bypass is attempted.
- `DirectWebsiteAdapter`: wraps a site search plugin for Daraz, Pickaboo, Star Tech, and Gadget & Gear. It fetches real search pages through the SSRF-safe HTTP fetcher.
- `CustomIndexAdapter`: queries `indexed_documents` using PostgreSQL full-text-style token matching over title/content. It is an operator-managed corpus, not a crawler.

The registry is built from settings; routes and orchestration do not import concrete adapters.

## Extraction Flow

`ExtractionService.extract_url`:

1. Validate URL syntax and allowed scheme/port.
2. Compute an extraction cache key from normalized URL and content options.
3. Fetch through `HttpFetcher` under HTTP extraction concurrency.
4. Parse with `ExtractionPipeline`.
5. If the static result lacks a product price, main text, or both title and text, optionally render with the browser pool when `BROWSER_FALLBACK=true` and `allow_browser=true`.
6. Score static and browser payloads and keep the richer one.
7. Cache and return the payload.

`ExtractionPipeline` applies a fixed priority:

1. JSON-LD
2. Microdata
3. RDFa
4. Open Graph / HTML metadata
5. Conservative HTML heuristics
6. Domain-specific extraction plugin
7. Optional AI fallback (disabled unless an `AIExtractor` is supplied)

Lower-priority sources fill only empty or placeholder fields. Prices retain explicit `price_source`, `verified`, and `verification_method` values. Provenance records each extracted field independently.

Batch extraction runs up to 20 URLs concurrently and returns per-item errors without failing the entire batch.

## Browser Pool

`BrowserPool` lazily creates up to `BROWSER_INSTANCES` Chromium processes with `BROWSER_PAGES_PER_INSTANCE` pages each. It exposes rendered HTML with navigation and post-load waits. Metrics track active pages and running instances. The pool is used by Google search and extraction fallback.

Browser consumption is resource intensive. The worker and API processes each have independent pools; size them according to available memory.

## Cache and Coordination

`CacheManager` supports Redis and memory backends:

- search and extraction cache-aside
- stale-while-revalidate
- request coalescing and lock-based stampede prevention
- cache bypass and forced refresh
- source-health cache
- auth snapshot cache

Redis also provides:

- sliding-window rate limits
- distributed semaphore leases
- shared circuit-breaker state

Memory mode cannot provide cross-process limits and must not be used for multi-process production.

## Background Jobs

A search or extraction request with `"async": true` creates a row in `extraction_jobs` and enqueues an ARQ function:

- `process_search_job`
- `process_extraction_job`
- `process_browser_job`

Retryable application errors are retried up to `JOB_MAX_RETRIES`. Jobs expose queued/running/succeeded/failed status and the same result shape as the synchronous endpoint.

## Persistence

PostgreSQL stores:

- accounts, applications, API keys
- API usage logs
- search history
- background jobs
- source metadata and source health snapshots
- internal indexed documents
- system settings

SQLAlchemy uses async sessions. Alembic owns schema migrations. UUID and timestamp mixins provide stable IDs and audit fields.

## Observability

Prometheus metrics include request counts/latency, search latency by mode/type/cache state, extraction latency, adapter success/failure, cache hits/misses, queue depth, browser pages/instances, and database/Redis health.

Structured JSON logs include request ID, route, status, duration, and account context where available. Sentry is optional and only initialized when `SENTRY_DSN` is set.

## Failure Boundaries

- Invalid client input: `422` or `400` with a structured error.
- Authentication/authorization: `401` or `403`.
- Rate/quota: `429`.
- Upstream timeout: source status or `504`; partial results are retained when possible.
- CAPTCHA/access denied: explicit `blocked` source status or error; never bypassed.
- Database/cache unavailable: `503` for operations that cannot proceed safely.
- Browser/concurrency saturation: `503 service_unavailable`.
- Unexpected failure: `500 internal_error`, logged with a request ID.

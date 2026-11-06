# Performance

## Scope

Performance tests use k6 against a running local stack. The normal pytest suite is deterministic and does not perform load testing.

The included script is `tests/performance/k6_load_test.js`. It supports:

| Workload | Behavior |
| --- | --- |
| `search` | Fixed query with cache enabled |
| `search-cache` | Repeated fixed query to measure cache hits |
| `search-live` | Unique query and forced refresh to exercise live adapters |
| `product` | Product search path and price filtering |
| `extract` | Single URL extraction with cache enabled |
| `extract-live` | Forced refresh extraction |
| `async-search` | Enqueue search and poll the job endpoint for worker load |
| `mixed` | Search, product, extraction, and live search rotation |

No fixed latency numbers are claimed in this repository. Results depend on hardware, cache state, adapter availability, upstream latency, browser capacity, and database/Redis placement. Record measured results in your own environment.

## Prerequisites

- Running API with PostgreSQL and Redis.
- All workers for async scenarios.
- A valid API key.
- k6 installed.
- A stable test URL for extraction that you are permitted to access.
- Google/direct-site access only if you are permitted to use those sources.

Create a key:

```bash
python -m scripts.create_api_key --name "Performance" --rate-limit 10000 --daily-quota 100000
```

Use a high enough limit for the intended test; otherwise `429` responses dominate the run.

## Baseline Commands

Set environment variables:

```bash
export SERP_BASE_URL="http://localhost:8000"
export SERP_API_KEY="sk_live_..."
export TEST_URL="https://example.com/"
```

Cache-hit search, 10 RPS for 2 minutes:

```bash
API_KEY="$SERP_API_KEY" \
BASE_URL="$SERP_BASE_URL" \
WORKLOAD=search-cache \
RATE=10 \
DURATION=2m \
k6 run tests/performance/k6_load_test.js
```

Sustained 10 RPS for 30 minutes:

```bash
API_KEY="$SERP_API_KEY" \
BASE_URL="$SERP_BASE_URL" \
WORKLOAD=search-cache \
RATE=10 \
DURATION=30m \
k6 run tests/performance/k6_load_test.js
```

Live search:

```bash
API_KEY="$SERP_API_KEY" \
BASE_URL="$SERP_BASE_URL" \
WORKLOAD=search-live \
RATE=2 \
DURATION=5m \
MODE=quick \
k6 run tests/performance/k6_load_test.js
```

Extraction:

```bash
API_KEY="$SERP_API_KEY" \
BASE_URL="$SERP_BASE_URL" \
WORKLOAD=extract \
EXTRACT_URL="$TEST_URL" \
RATE=5 \
DURATION=10m \
k6 run tests/performance/k6_load_test.js
```

Worker saturation:

```bash
API_KEY="$SERP_API_KEY" \
BASE_URL="$SERP_BASE_URL" \
WORKLOAD=async-search \
RATE=5 \
DURATION=10m \
k6 run tests/performance/k6_load_test.js
```

## Configuration Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `BASE_URL` | `http://localhost:8000` | API base URL |
| `API_KEY` | required | API key |
| `WORKLOAD` | `mixed` | Scenario from the table above |
| `RATE` | `10` | Requests per second |
| `DURATION` | `30m` | Scenario duration |
| `MAX_VUS` | `200` | Maximum virtual users |
| `LIMIT` | `10` | Search result limit |
| `MODE` | `quick` | Search mode |
| `COUNTRY` | `US` | Search country |
| `LANGUAGE` | `en` | Search language |
| `EXTRACT_URL` | `https://example.com/` | Extraction target |
| `ALLOW_BROWSER` | `false` | Allow browser fallback for extraction |
| `SEARCH_QUERY` | `wireless headphones` | Base query |
| `P95_MS` | `20000` | k6 p95 threshold |
| `MAX_ERROR_RATE` | `0.05` | Allowed HTTP/workflow error rate |
| `POLL_ATTEMPTS` | `30` | Async job poll attempts |
| `POLL_INTERVAL` | `0.5` | Seconds between job polls |

Adjust thresholds after establishing a baseline. Setting thresholds that the environment cannot meet creates a failing run, not a faster service.

## Metrics to Capture

k6 reports:

- `http_req_duration` p50/p90/p95/p99
- `http_req_failed`
- requests per second
- `search_latency`
- `extraction_latency`
- `cache_hits` and `cache_misses`
- `workflow_failures`

Capture service-side metrics during the same window:

- CPU and memory per API/worker process
- PostgreSQL connections and slow queries
- Redis memory, evictions, and latency
- `serp_queue_depth`
- `serp_browser_instances` and `serp_active_browser_pages`
- `serp_cache_hits_total` and `serp_cache_misses_total`
- `serp_adapter_failures_total`
- `serp_api_request_duration_seconds`
- `serp_search_duration_seconds`
- `serp_extraction_duration_seconds`

Resource snapshot while the test runs:

```bash
# Linux/macOS
ps -eo pid,pcpu,pmem,rss,cmd --sort=-pcpu | head -20

# Windows PowerShell
Get-Process python | Sort-Object CPU -Descending | Select-Object -First 20 Id,CPU,WorkingSet,ProcessName
```

## Report Template

Record results with the environment and configuration used:

```text
Date:
Commit:
Host / process limits:
PostgreSQL / Redis placement:
Workload:
Rate:
Duration:
Search mode:
Cache state:
Browser enabled:
Live upstreams:

Requests:
Error rate:
p50 latency:
p95 latency:
p99 latency:
Cache hit rate:
CPU peak:
Memory peak:
Queue depth peak:
Adapter failures:
Database connections peak:
Redis evictions:
Notes:
```

Do not compare runs with different cache state, adapter set, hardware, or upstream conditions as if they were equivalent.

## Test Matrix

At minimum, cover:

1. Cache-hit search at 10 RPS.
2. Live search at a conservative rate.
3. Single extraction at 5 RPS.
4. Async search with workers enabled.
5. A 30-minute sustained run to observe memory and connection growth.
6. Source timeout behavior with a deliberately slow or unavailable source in an isolated test environment.
7. Worker saturation by reducing worker concurrency or increasing async arrival rate.
8. Resource cleanup after stopping the load: browser instances, queue depth, database connections, and file descriptors should return to baseline.

## Expected Bottlenecks

- Google/browser search: Chromium CPU/memory, navigation latency, CAPTCHA/blocking, and page waits.
- Direct website search: upstream HTML size, site latency, selector changes, and rate limiting.
- Extraction: outbound HTTP latency, response size, parser CPU, and optional browser rendering.
- PostgreSQL: auth lookups, usage writes, history writes, and internal-index ILIKE scans.
- Redis: rate-limit scripts, distributed semaphores, cache payload size, and eviction policy.
- API workers: request body size, connection pool exhaustion, and deep-search fan-out.

## Tuning Levers

- `GLOBAL_SEARCH_CONCURRENCY`
- `PER_ADAPTER_CONCURRENCY`
- `HTTP_EXTRACTION_CONCURRENCY`
- `PER_DOMAIN_CONCURRENCY`
- `BROWSER_INSTANCES`
- `BROWSER_PAGES_PER_INSTANCE`
- `DB_POOL_SIZE` and `DB_MAX_OVERFLOW`
- `REDIS_MAX_CONNECTIONS`
- cache TTLs
- `QUICK_SEARCH_TIMEOUT`, `DEEP_SEARCH_TIMEOUT`, `ADAPTER_TIMEOUT`, `EXTRACTION_TIMEOUT`
- `SEARCH_RETRIES`, circuit-breaker threshold/cooldown
- worker `max_jobs` via queue/worker configuration

Increase concurrency only after confirming the bottleneck is not an upstream limit, database pool, Redis latency, or browser memory.

## Safety

- Do not run sustained live Google or third-party searches without permission.
- Do not use performance tests to bypass rate limits or access controls.
- Use test-owned URLs and internal fixtures where possible.
- Keep request and response limits enabled during tests.
- A load test is not a security test. See [SECURITY.md](SECURITY.md).

# Troubleshooting

## First Checks

```bash
curl -fsS http://127.0.0.1:8000/v1/health
curl -fsS http://127.0.0.1:8000/v1/ready
alembic current
redis-cli ping
```

Check the terminal or process supervisor logs for the API and each worker. There are no container logs.

Use a request ID for log correlation:

```bash
curl -i http://localhost:8000/v1/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "X-Request-ID: debug-001" \
  -H "Content-Type: application/json" \
  -d '{"query":"test","limit":1}'
```

## API Will Not Start

### `ModuleNotFoundError`

Install the project and development extras:

```bash
python -m pip install -e ".[dev]"
```

Confirm the interpreter:

```bash
python -c "import sys; print(sys.executable)"
python -c "import fastapi, sqlalchemy, redis; print('imports ok')"
```

### Configuration parsing error

Check JSON-shaped settings:

- `GOOGLE_SELECTORS_JSON` must be a JSON object.
- `RANKING_WEIGHTS_JSON` must be a JSON object with numeric values.
- List settings accept comma-separated values or JSON arrays.

Validate directly:

```bash
python -c "from app.config import Settings; print(Settings().model_dump())"
```

### Port already in use

Change the host port:

```bash
uvicorn app.main:app --port 8001
```

## Database Issues

### Startup warns `database_not_ready_at_startup`

The API still starts, but `/v1/ready` will fail until PostgreSQL is reachable. Check the PostgreSQL service and logs:

```bash
# Linux
sudo systemctl status postgresql
sudo journalctl -u postgresql --tail=100

# Windows PowerShell
Get-Service postgresql*
```

Confirm `.env` points at the actual PostgreSQL host:

```dotenv
DATABASE_URL=postgresql+asyncpg://serp:serp@localhost:5432/serp
```

### Migration failure

```bash
alembic current
alembic history
alembic upgrade head
```

If a migration is partially applied, inspect the database and migration state before manual changes. Do not run a downgrade on a database with unbacked production data.

### `relation ... does not exist`

Migrations have not run. Apply them manually before starting the API:

```bash
alembic upgrade head
```

## Redis and Cache Issues

### `redis_unavailable` or `/v1/ready` cache failure

```bash
redis-cli -u redis://localhost:6379/0 ping
```

Check the Redis service logs or the terminal where `redis-server` is running. Use the actual host in `REDIS_URL`; for a local service the default is `redis://localhost:6379/0`.

### Rate limits are inconsistent across processes

You are using the memory backend. Set:

```dotenv
CACHE_BACKEND=redis
```

Memory rate limits are process-local and intended for tests/single-process development.

### Cache appears stale

- Search fresh TTL: `SERP_CACHE_TTL` or `PRODUCT_SERP_CACHE_TTL`
- Search stale window: `SERP_CACHE_STALE_TTL` or `PRODUCT_SERP_STALE_TTL`
- Extraction: `EXTRACTION_CACHE_TTL` / `EXTRACTION_STALE_TTL`
- Force a fresh request with `"force_refresh": true` or `"use_cache": false`.

## Authentication Problems

### `401 authentication_failed`

- Confirm the key is sent as `X-API-Key` or `Authorization: Bearer`.
- Confirm the raw key was copied completely.
- Confirm `API_KEY_PEPPER` has not changed since the key was created.
- Confirm the key has not expired.

Create a new key:

```bash
python -m scripts.create_api_key --name "Debug" --expires-days 1
```

### `403 forbidden`

Check for:

- revoked API key (`revoked_at` set)
- disabled account (`accounts.is_active` false)
- disabled application (`applications.is_active` false)

Inspect the relevant rows with your normal database tooling. Do not log or copy raw keys.

### `429 rate_limit_exceeded` or `quota_exceeded`

- Per-key limit overrides account, which overrides `DEFAULT_RATE_LIMIT_PER_MINUTE`.
- Daily quota resets at UTC midnight.
- `force_refresh` and large batches consume more quota/usage.
- Use `GET /v1/account/usage` to inspect current period usage.
- For load testing, create a dedicated high-limit key.

## Search Problems

### `503 search_source_unavailable`

No enabled adapter supports the requested `search_type`. Check:

```bash
curl -sS http://localhost:8000/v1/sources -H "X-API-Key: $SERP_API_KEY"
curl -sS http://localhost:8000/v1/sources/health -H "X-API-Key: $SERP_API_KEY"
```

Notes:

- Google supports `web`, `products`, and `news`.
- Direct website adapters support `web` and `products`.
- The internal index supports `web`, `products`, and `news`.
- No built-in adapter supports `images`.
- Direct adapters require `DIRECT_WEBSITES_ENABLED=true`.
- The internal index requires `CUSTOM_INDEX_ENABLED=true` and seeded documents.
- Google requires `GOOGLE_ENABLED=true` and Playwright Chromium.

### Google returns `captcha_detected` or `access_denied`

This is an expected upstream boundary. The service does not bypass CAPTCHA or access denial. Use another enabled source or reduce request pressure. Do not attempt to defeat site protections.

### Direct adapter returns no results

- Verify the site search URL still works in a browser.
- The site may have changed selectors or markup.
- The site may block automated access.
- Add/update a plugin selector and a fixture-based test.
- Confirm the adapter is enabled and listed in `/v1/sources`.

### Internal index returns nothing

Seed documents:

```bash
python -m scripts.seed
python -m scripts.seed --index-file documents.json
```

Verify `indexed_documents` contains rows and `CUSTOM_INDEX_ENABLED=true`.

### Deep search has no extraction data

- Deep search extraction is bounded by `DEEP_SEARCH_EXTRACT_LIMIT`.
- Extraction requires outbound access to result URLs.
- Browser fallback may be unavailable.
- Some results may be marked `pending` or `failed` with warnings.
- Check `results[].extraction_status` and `warnings`.

## Extraction Problems

### `502 extraction_failed`

Common causes:

- upstream timeout
- non-HTML content type
- response over `HTTP_MAX_RESPONSE_BYTES`
- upstream 4xx/5xx
- redirect failure

Check the request ID in logs. The API intentionally does not expose raw upstream content.

### `400 ssrf_blocked`

Expected for:

- URL credentials
- non-HTTP/HTTPS schemes
- ports outside `HTTP_ALLOWED_PORTS`
- private/loopback/link-local/reserved addresses
- redirects to blocked targets

Do not set `SSRF_ALLOW_PRIVATE=true` on an internet-facing service. If testing locally, use the test harness rather than weakening production configuration.

### Browser fallback unavailable

```bash
python -m playwright install chromium
```

On Linux, also install browser dependencies:

```bash
python -m playwright install --with-deps chromium
```

Confirm `BROWSER_FALLBACK=true`. Browser extraction is disabled when `allow_browser=false`.

### Empty or partial page

- Some sites render content only with JavaScript; enable browser fallback.
- Paywalls and login walls are not bypassed.
- Structured data may be absent; heuristics can only extract visible content.
- Check `data.status`, `data.warnings`, and `data.provenance`.
- Do not treat missing prices as zero; the API reports missing values instead of inventing them.

## Background Jobs

### Job remains `queued`

- Confirm Redis is running.
- Confirm the correct worker is running and consuming the right queue.
- Check worker logs.

```bash
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

Check the terminal or process-supervisor logs for each worker. Start any missing worker from the project root:

```bash
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

### Job is `failed`

`GET /v1/jobs/{job_id}` returns the stored error. Retryable application errors are retried up to `JOB_MAX_RETRIES`; non-retryable errors fail immediately. Fix the underlying source/configuration rather than replaying indefinitely.

### `503 worker_unavailable`

The API could not enqueue to Redis. Confirm Redis connectivity and that the API is using the same Redis database as workers.

## Native Service Issues

### Browser worker crashes or tabs fail

- Install Chromium with `python -m playwright install chromium`.
- On Linux, install system dependencies with `python -m playwright install --with-deps chromium`.
- Ensure adequate shared memory and RAM for Chromium.
- Reduce `BROWSER_INSTANCES` or `BROWSER_PAGES_PER_INSTANCE` on memory-constrained hosts.
- Check for leftover Chromium processes after an unclean shutdown.

### API readiness check fails

```bash
curl -v http://127.0.0.1:8000/v1/ready
alembic current
redis-cli ping
```

Check PostgreSQL/Redis availability and migration completion.

### Secrets in `.env`

Development defaults must be replaced in production:

- `SECRET_KEY`
- `API_KEY_PEPPER`
- PostgreSQL credentials
- Redis credentials

Do not expose PostgreSQL or Redis publicly. Restrict `.env` permissions to the service account.

## Test Failures

### Tests pass locally but fail in CI

- Use the same Python version and dependency lock/install command.
- Confirm tests do not depend on live network.
- Do not share a persistent SQLite file between runs.
- Confirm no test mutates module-level settings or caches without cleanup.

### One extractor test fails after an HTML change

Re-run:

```bash
python -m pytest tests/unit/test_extractors.py -vv
```

Inspect fixture HTML and whether the change is in structured data, metadata, or heuristics. Preserve JSON-LD before running `_main_text`; the pipeline deliberately reparses a copy so script nodes are not destroyed.

### Ruff or mypy failure

```bash
python -m ruff check . --fix
python -m ruff format .
python -m mypy app
```

Do not add blanket ignores. Add a narrow ignore only for a verified third-party typing limitation.

## Metrics and Monitoring

- Metrics endpoint: `/metrics` when `METRICS_ENABLED=true`.
- No Prometheus or Grafana configuration files are included.
- Point an existing Prometheus server at the API's `/metrics` endpoint if needed.
- Alert on `/v1/ready`, API latency, error rate, adapter failures, queue depth, browser health, and process restarts.

If metrics are empty, confirm `METRICS_ENABLED=true` and that the API is running and reachable from your Prometheus instance.

## Getting More Help

Collect:

- request ID
- endpoint and status code
- sanitized request shape (never the API key)
- `/v1/health` and `/v1/ready` output
- relevant service logs
- configuration values with secrets removed
- whether the issue reproduces with a local fixture or only a live upstream

# Universal SERP API

An independent, provider-agnostic search discovery, SERP collection, extraction, and structured-data API. The service combines configurable search adapters, a deterministic extraction pipeline, Redis-backed caching and coordination, PostgreSQL persistence, and ARQ background workers.

The API does not require a paid SERP provider. Optional integrations and AI extraction are opt-in. CAPTCHA, paywall, authentication, and access-control bypasses are not implemented.

## Features

- Async FastAPI application with OpenAPI documentation at `/docs`, ReDoc at `/redoc`, and `/openapi.json`.
- Search adapters are isolated behind a stable contract: Google browser adapter, direct website plugins, and an operator-managed PostgreSQL document index.
- Quick and deep search modes, with optional deep extraction of top results.
- Product search with price filtering and explicitly tracked price provenance.
- Universal extraction for products, articles, local businesses, media, visible text, JSON-LD, microdata, and RDFa.
- Optional website-specific extraction plugins without modifying the core pipeline.
- HMAC-hashed API keys, per-key/account rate limits, daily quotas, request IDs, and structured errors.
- SSRF protection with DNS resolution checks, private-address blocking, redirect validation, port restrictions, and response size limits.
- Redis cache with stale-while-revalidate, stampede prevention, and distributed concurrency leases.
- Background jobs for search, extraction, and browser-rendered extraction.
- Prometheus metrics, optional Sentry, structured JSON logs, and HTTP health checks.
- SQLite-based automated tests with no live Google or arbitrary external website dependency.

## Architecture at a Glance

```text
Client
  -> FastAPI middleware (request ID, body limit, access log, security headers)
  -> Authentication, rate limit, quota
  -> Search orchestrator or extraction service
  -> Cache / concurrency / adapters / browser pool
  -> Normalization, deduplication, ranking, provenance
  -> PostgreSQL history, usage, jobs, source health
```

ARQ workers provide asynchronous search and extraction:

- `serp:search` -> `app.workers.search_worker.WorkerSettings`
- `serp:extraction` -> `app.workers.extraction_worker.WorkerSettings`
- `serp:browser` -> `app.workers.browser_worker.WorkerSettings`

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full component and request flow.

## Requirements

- Python 3.13 or newer. The suite is tested with Python 3.14 on Windows.
- PostgreSQL 16 or newer.
- Redis 7 or newer.
- Playwright Chromium for live browser collection and browser extraction.

No Docker, Compose, VPS, Nginx, domain, or TLS configuration is included.

## Quick Start

The complete native setup is in [RUN_GUIDE.md](RUN_GUIDE.md). Short version:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX:   source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Create the PostgreSQL role/database and start Redis, then configure `.env`:

```bash
cp .env.example .env
alembic upgrade head
```

Create the first account and API key:

```bash
python -m scripts.create_api_key \
  --name "Local Development" \
  --email local@example.test \
  --expires-days 365
```

Store the returned `api_key` immediately. Only the key hash is persisted; the raw key cannot be retrieved again.

Start the API and workers in separate terminals:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

Check the services:

```bash
curl http://127.0.0.1:8000/v1/health
curl http://127.0.0.1:8000/v1/ready
```

Open:

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Prometheus metrics: <http://127.0.0.1:8000/metrics>

Prometheus/Grafana provisioning files are not included. Point your own Prometheus scraper at `/metrics` if needed.

## Basic API Usage

All protected endpoints require either `X-API-Key: <key>` or `Authorization: Bearer <key>`.

```bash
export SERP_API_KEY="sk_live_..."
```

Search:

```bash
curl -sS http://localhost:8000/v1/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "wireless headphones",
    "country": "US",
    "language": "en",
    "limit": 10,
    "mode": "quick"
  }'
```

Extract one URL:

```bash
curl -sS http://localhost:8000/v1/extract \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/product/widget",
    "include_text": true,
    "include_media": true,
    "allow_browser": true
  }'
```

Run work asynchronously:

```bash
curl -sS http://localhost:8000/v1/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "mechanical keyboard", "async": true}'
```

Poll the returned job:

```bash
curl -sS http://localhost:8000/v1/jobs/<job_id> \
  -H "X-API-Key: $SERP_API_KEY"
```

## Public Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/v1/search` | Web, news, or configured-source search (no image adapter by default) |
| `POST` | `/v1/products/search` | Product search with price filtering |
| `POST` | `/v1/extract` | Single URL extraction |
| `POST` | `/v1/extract/batch` | Up to 20 URL extractions |
| `GET` | `/v1/jobs/{job_id}` | Background job status and result |
| `GET` | `/v1/account` | Current account and API-key metadata |
| `GET` | `/v1/account/usage` | Aggregated usage statistics |
| `GET` | `/v1/sources` | Enabled search sources and capabilities |
| `GET` | `/v1/sources/health` | Bounded source health checks |
| `GET` | `/v1/health` | Liveness |
| `GET` | `/v1/ready` | Database and cache readiness |

Full request and response examples: [docs/API_DOCUMENTATION.md](docs/API_DOCUMENTATION.md).

## Local Development

Create and activate a virtual environment, then install the application and development tools:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX:   source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

Start PostgreSQL and Redis as native services. See [RUN_GUIDE.md](RUN_GUIDE.md) for platform-specific service commands.

Use `.env` with local service URLs:

```dotenv
DATABASE_URL=postgresql+asyncpg://serp:serp@localhost:5432/serp
REDIS_URL=redis://localhost:6379/0
```

Apply migrations and start the API:

```bash
alembic upgrade head
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Create a key and seed source metadata:

```bash
python -m scripts.create_api_key --name "Local Development" --email local@example.test
python -m scripts.seed
```

Start workers in separate terminals:

```bash
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

No Google access is required for tests. Live Google collection is best-effort and may encounter CAPTCHA or access restrictions; such responses are surfaced as structured errors and are not bypassed.

## Configuration

Configuration is environment driven. See `.env.example` for the complete list, including:

- secrets and database/Redis URLs
- cache TTLs and cache backend
- rate limits and quotas
- search, extraction, and request deadlines
- concurrency limits
- outbound HTTP and SSRF policy
- Google and direct-website adapter settings
- ranking weights
- worker queues and retry limits
- metrics, Sentry, and logging

Important production checks:

- Replace `SECRET_KEY`, `API_KEY_PEPPER`, and database/Redis credentials.
- Restrict `ALLOWED_HOSTS` and set an explicit `CORS_ORIGINS` list.
- Keep `SSRF_ALLOW_PRIVATE=false`.
- Use `CACHE_BACKEND=redis` for shared limits, queues, coordination, and auth caching.
- Run all three workers.
- Terminate TLS at a local reverse proxy if the service is shared beyond localhost.
- Do not enable direct adapters or Google unless egress and site terms permit it.

## Testing and Quality

Run the complete suite:

```bash
python -m pytest -q
```

Coverage:

```bash
python -m pytest --cov=app --cov-report=term-missing
```

Static checks:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy app
python -m compileall -q app migrations tests scripts
```

Install and run pre-commit:

```bash
pre-commit install
pre-commit run --all-files
```

Performance testing uses k6 and the script in `tests/performance/`:

```bash
API_KEY="$SERP_API_KEY" BASE_URL="http://localhost:8000" \
WORKLOAD=search-cache RATE=10 DURATION=2m \
k6 run tests/performance/k6_load_test.js
```

See [docs/TESTING.md](docs/TESTING.md) and [docs/PERFORMANCE.md](docs/PERFORMANCE.md).

## Migrations and Operators

```bash
alembic current
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "describe change"
```

Seed sources and optionally import internal-index documents:

```bash
python -m scripts.seed
python -m scripts.seed --index-file documents.json
```

The index file must be a JSON array. Each document requires `url` and `title`; `content`, `canonical_url`, `language`, `page_type`, and `metadata` are optional.

## Project Layout

```text
app/
  adapters/       search and AI adapter contracts
  api/            middleware, dependencies, routes
  core/           cache, concurrency, security, orchestration, errors
  database/       async engine/session setup
  extractors/     HTTP/browser fetching and extraction pipeline
  models/         SQLAlchemy models
  monitoring/     metrics and Sentry
  processors/     normalization, deduplication, ranking, price parsing
  schemas/        Pydantic request/response contracts
  services/       auth, extraction, jobs, usage
  workers/        ARQ task and worker definitions
migrations/       Alembic environment and versions
scripts/          API key creation and source/index seeding
tests/            unit, integration, security, and performance assets
docs/             architecture, API, operations, and guides
```

## Documentation

- [Run Guide (development and production)](RUN_GUIDE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [API Documentation](docs/API_DOCUMENTATION.md)
- [Development](docs/DEVELOPMENT.md)
- [Search Adapter Guide](docs/SEARCH_ADAPTER_GUIDE.md)
- [Extraction Guide](docs/EXTRACTION_GUIDE.md)
- [Database Schema](docs/DATABASE_SCHEMA.md)
- [Security](docs/SECURITY.md)
- [Performance](docs/PERFORMANCE.md)
- [Testing](docs/TESTING.md)
- [Troubleshooting](docs/TROUBLESHOOTING.md)

## Operational Limitations

- Google and direct website adapters depend on external page structure, network access, and site policy. Their live behavior is not guaranteed by the automated test suite.
- The internal index is not a web crawler; operators must populate it with `scripts/seed.py`.
- Browser extraction requires Playwright Chromium and is resource intensive.
- Redis memory mode is intended for tests and single-process development only.
- No deployment files are included. Production requires external PostgreSQL/Redis services and a process supervisor; TLS and reverse-proxy configuration are outside this project.

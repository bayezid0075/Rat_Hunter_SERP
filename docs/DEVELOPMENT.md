# Development Guide

See [../RUN_GUIDE.md](../RUN_GUIDE.md) for complete native development and production service commands.

## Prerequisites

- Python 3.13 or newer. The test suite is also run on Python 3.14.
- PostgreSQL 16 and Redis 7 for full-stack development.
- Playwright Chromium for Google search and browser extraction fallback.
- Optional: k6 for performance testing.

## Initial Setup

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# Windows cmd
.venv\Scripts\activate.bat

# POSIX
source .venv/bin/activate
```

Install:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

For Linux hosts, use `python -m playwright install --with-deps chromium` if system browser libraries are missing.

Copy configuration and replace development secrets:

```bash
cp .env.example .env
```

Point services at localhost:

```dotenv
DATABASE_URL=postgresql+asyncpg://serp:serp@localhost:5432/serp
REDIS_URL=redis://localhost:6379/0
ENVIRONMENT=development
```

Start native PostgreSQL and Redis services before applying migrations. Platform-specific commands are in [../RUN_GUIDE.md](../RUN_GUIDE.md).

## Database Migrations

Apply the schema:

```bash
alembic upgrade head
```

Inspect state:

```bash
alembic current
alembic history
```

Create a migration after changing SQLAlchemy models:

```bash
alembic revision --autogenerate -m "describe change"
```

Review generated migrations before applying them. Downgrade one revision:

```bash
alembic downgrade -1
```

The test suite creates its own SQLite schema from the SQLAlchemy metadata and does not depend on a running PostgreSQL instance.

## Create an API Key

```bash
python -m scripts.create_api_key \
  --name "Local Development" \
  --email local@example.test \
  --expires-days 365
```

The command prints the raw key once. Use `--application`, `--rate-limit`, and `--daily-quota` for overrides. Run the same command from the project root on the host where the application is installed.

## Seed Sources and the Internal Index

```bash
python -m scripts.seed
```

Seed an internal document corpus from JSON:

```bash
python -m scripts.seed --index-file documents.json
```

Example:

```json
[
  {
    "url": "https://example.com/guide",
    "title": "Example guide",
    "content": "Text searchable by the internal index.",
    "language": "en",
    "page_type": "article",
    "metadata": {"category": "guide"}
  }
]
```

## Run the API

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

`Settings` is cached per process with `lru_cache`; restart the process after configuration changes when reload is not active.

Useful URLs:

- `http://localhost:8000/docs`
- `http://localhost:8000/redoc`
- `http://localhost:8000/openapi.json`
- `http://localhost:8000/v1/ready`
- `http://localhost:8000/metrics`

## Run Workers

Start each worker in a separate terminal:

```bash
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

Workers require Redis. Browser extraction also requires Playwright Chromium.

## Code Layout Rules

- Keep provider-specific behavior in `app/adapters/` or `app/extractors/plugins/`.
- Keep orchestration and policy in `app/core/`, `app/services/`, and `app/processors/`.
- Keep request/response contracts in `app/schemas/`.
- Add operational settings to `app/config.py` and `.env.example` together.
- Do not import concrete search adapters outside the registry construction.
- Do not add blocking I/O to async paths.
- Do not log raw API keys, secrets, full request bodies, or sensitive page content.
- Do not weaken SSRF, authentication, rate-limit, or quota checks for tests.

## Testing

```bash
python -m pytest -q
python -m pytest tests/unit -q
python -m pytest tests/integration -q
python -m pytest tests/security -q
python -m pytest --cov=app --cov-report=term-missing
```

No normal test requires live Google access or arbitrary external websites. Integration tests use an in-process app, SQLite, memory cache, fake adapters, and stub HTTP fetching.

## Static Checks

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy app
python -m compileall -q app migrations tests scripts
```

Apply Ruff fixes:

```bash
python -m ruff check . --fix
python -m ruff format .
```

Pre-commit:

```bash
pre-commit install
pre-commit run --all-files
```

## Changing Configuration

1. Add a typed field to `Settings` in `app/config.py`.
2. Add the matching uppercase variable to `.env.example`.
3. Pass the value through dependency construction, not through global state.
4. Update the relevant documentation.
5. Add or update tests for behavior that changes.

Configuration values are case-insensitive. List fields accept comma-separated strings or JSON arrays. `GOOGLE_SELECTORS_JSON` and `RANKING_WEIGHTS_JSON` must be JSON objects.

## Debugging

- Add `X-Request-ID` to reproduce a trace through logs.
- Check `/v1/ready` before blaming request logic.
- Check `/v1/sources/health` for adapter-level issues.
- Use `LOG_JSON=false` for readable local logs.
- Use `LOG_LEVEL=DEBUG` sparingly; upstream pages and client content must not be logged wholesale.
- Use the fake adapters and stub fetchers in `tests/support.py` for deterministic regression tests.

## Common Development Workflows

### Add an endpoint

1. Add or extend schemas in `app/schemas/`.
2. Add the route under `app/api/routes/`.
3. Wire services through `app/api/dependencies.py`.
4. Register the router in `app/main.py`.
5. Add integration tests.
6. Document request/response examples in `docs/API_DOCUMENTATION.md`.

### Add a search source

Follow [SEARCH_ADAPTER_GUIDE.md](SEARCH_ADAPTER_GUIDE.md). Never place site selectors or URLs in the orchestrator.

### Add an extraction plugin

Follow [EXTRACTION_GUIDE.md](EXTRACTION_GUIDE.md). Plugins are additive; they must not mutate shared parser state or fabricate missing values.

### Change the database

1. Update `app/models/`.
2. Generate and review an Alembic migration.
3. Update `docs/DATABASE_SCHEMA.md`.
4. Run migrations and the full test suite.

## Documentation Synchronization

When behavior changes, update all affected files:

- `README.md` for setup and top-level behavior
- `docs/API_DOCUMENTATION.md` for endpoints and payloads
- `docs/ARCHITECTURE.md` for component/flow changes
- `docs/SECURITY.md` for trust boundaries and controls
- `docs/DATABASE_SCHEMA.md` for models/migrations
- `docs/TESTING.md` and `docs/PERFORMANCE.md` for verification changes
- `docs/TROUBLESHOOTING.md` for new failure modes

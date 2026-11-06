# Project Run Guide (Windows)

This guide is written for this Windows PC only. It contains no Linux, macOS, or POSIX instructions.

This project runs as native local services. Docker, Compose, Prometheus/Grafana provisioning, and production deployment files are intentionally not included.

Components:

- FastAPI API: one or more Uvicorn processes.
- PostgreSQL: persistent application data.
- Redis: cache, rate limits, distributed concurrency, and ARQ queues.
- Three ARQ worker processes: search, HTTP extraction, and browser extraction.
- Playwright Chromium: required for Google and browser extraction.

Machine-specific paths used in this guide:

```text
Project root:  C:\Users\Bayezid\Desktop\Rat_Hunter_SERP
Python 3.14:   C:\Users\Bayezid\AppData\Local\Python\pythoncore-3.14-64\python.exe
PostgreSQL 18: C:\Program Files\PostgreSQL\18
psql.exe:      C:\Program Files\PostgreSQL\18\bin\psql.exe
Redis:         C:\Program Files\Redis
```

## 1. Prerequisites

- Python 3.13 or newer. This PC has 3.14.6.
- PostgreSQL 16 or newer. This PC has PostgreSQL 18.
- Redis 7 or newer. This PC has Redis installed as a Windows service.
- Playwright Chromium.
- Windows Service Control Manager, NSSM, or Task Scheduler for production. No service unit files are included.

Check the tools in PowerShell:

```powershell
python --version
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" --version
& "C:\Program Files\Redis\redis-server.exe" --version
```

Check that the installed Windows services are running:

```powershell
Get-Service postgresql-x64-18
Get-Service Redis
```

Both services should show `Running`. If not, start them:

```powershell
Start-Service postgresql-x64-18
Start-Service Redis
```

Optional: add the PostgreSQL and Redis folders to `PATH` for the current PowerShell session so `psql` and `redis-cli` work without full paths:

```powershell
$env:Path += ";C:\Program Files\PostgreSQL\18\bin;C:\Program Files\Redis"
```

`psql` is not on `PATH` by default on this PC; the rest of this guide uses the full path.

## 2. Install

From the project root in PowerShell. Use the base Python 3.14 interpreter explicitly. Do **not** run this while `.venv` is active; Windows cannot overwrite the venv's running `python.exe` and you will get `Unable to copy ... venvlauncher.exe`.

```powershell
Set-Location "C:\Users\Bayezid\Desktop\Rat_Hunter_SERP"
& "C:\Users\Bayezid\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m venv .venv
```

Alternatively use the Python launcher:

```powershell
py -3.14 -m venv .venv
```

Activate the environment. Pick the command for the shell you use:

PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, allow it for the current session only, then activate:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Command Prompt (`cmd.exe`):

```bat
.venv\Scripts\activate.bat
```

Git Bash (use forward slashes; backslashes are escape characters):

```bash
source .venv/Scripts/activate
```

Install dependencies and Chromium. Do not use `--with-deps` on Windows; that flag is for Linux package managers.

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m playwright install chromium
```

If a previous `.venv` exists and is broken, deactivate it, delete the folder, and recreate it:

```powershell
deactivate
Remove-Item -Recurse -Force .venv
& "C:\Users\Bayezid\AppData\Local\Python\pythoncore-3.14-64\python.exe" -m venv .venv
```

## 3. Configure PostgreSQL

The PostgreSQL 18 service (`postgresql-x64-18`) runs on port `5432`. Create the application role and database. Run these as a PostgreSQL administrator, from PowerShell:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE USER serp WITH PASSWORD 'serp';"
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "CREATE DATABASE serp OWNER serp;"
```

If the role already exists, change its password instead:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -c "ALTER USER serp WITH PASSWORD 'serp';"
```

Use a different password outside disposable local development. The `postgres` administrator password is the one set when PostgreSQL was installed; if you do not know it, reset authentication in `C:\Program Files\PostgreSQL\18\data\pg_hba.conf` temporarily or use pgAdmin.

The default connection string is:

```dotenv
DATABASE_URL=postgresql+asyncpg://serp:serp@localhost:5432/serp
```

Manage the Windows PostgreSQL service:

```powershell
Get-Service postgresql-x64-18
Start-Service postgresql-x64-18
Restart-Service postgresql-x64-18
Stop-Service postgresql-x64-18
```

## 4. Configure Redis

Redis is installed as the Windows service `Redis` and is already running on port `6379` with no password. Check it:

```powershell
Get-Service Redis
& "C:\Program Files\Redis\redis-cli.exe" ping
```

`PONG` means Redis is reachable. Manage the service:

```powershell
Start-Service Redis
Restart-Service Redis
Stop-Service Redis
```

To run Redis in the foreground for debugging instead of the service, stop the service first, then:

```powershell
& "C:\Program Files\Redis\redis-server.exe" "C:\Program Files\Redis\redis.windows.conf"
```

The default connection string for this PC (no password) is:

```dotenv
REDIS_URL=redis://localhost:6379/0
CACHE_BACKEND=redis
```

`CACHE_BACKEND=memory` is only for tests or a single development process. It is not a shared limit across processes.

If you configure Redis with authentication later:

```dotenv
REDIS_URL=redis://:password@localhost:6379/0
```

## 5. Create `.env`

Copy the example file from PowerShell:

```powershell
Copy-Item .env.example .env
```

From Command Prompt:

```bat
copy .env.example .env
```

From Git Bash:

```bash
cp .env.example .env
```

Edit at least these values:

```dotenv
SECRET_KEY=replace-with-a-long-random-value
API_KEY_PEPPER=replace-with-a-long-random-value
DATABASE_URL=postgresql+asyncpg://serp:serp@localhost:5432/serp
REDIS_URL=redis://localhost:6379/0
CACHE_BACKEND=redis
```

Do not commit `.env`. The example defaults are for local development only. Note that `.env.example` ships with `GOOGLE_ENABLED=true` and `LOG_JSON=true`; for local development you will likely want the overrides in section 8.

## 6. Apply Migrations

Run with the virtual environment active, before starting the API and after every schema change:

```powershell
alembic upgrade head
alembic current
```

Verify PostgreSQL tables as the `serp` user:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U serp -d serp -c "\dt"
```

## 7. Create an API Key and Seed Sources

```powershell
python -m scripts.create_api_key `
  --name "Local Development" `
  --email local@example.test `
  --expires-days 365
```

The backtick `` ` `` is the PowerShell line-continuation character. On one line:

```powershell
python -m scripts.create_api_key --name "Local Development" --email local@example.test --expires-days 365
```

Store the returned `api_key` immediately. Only the hash is stored.

Seed source metadata:

```powershell
python -m scripts.seed
```

Optionally import internal-index documents:

```powershell
python -m scripts.seed --index-file documents.json
```

## 8. Development Run

Start the API with reload. Keep it in its own PowerShell window:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Start each worker in a separate PowerShell window. Each window must activate `.venv` first:

```powershell
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

Typical development values in `.env`:

```dotenv
ENVIRONMENT=development
LOG_JSON=false
LOG_LEVEL=DEBUG
CACHE_BACKEND=redis
GOOGLE_ENABLED=false
DIRECT_WEBSITES_ENABLED=false
CUSTOM_INDEX_ENABLED=true
```

Disable Google/direct sources unless you are permitted to access them and intentionally testing live behavior.

Verify from PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/v1/health
Invoke-RestMethod http://127.0.0.1:8000/v1/ready
```

Or with `curl.exe` (built into Windows 10/11):

```powershell
curl.exe -i http://127.0.0.1:8000/v1/health
curl.exe -i http://127.0.0.1:8000/v1/ready
```

Open:

- Swagger UI: <http://127.0.0.1:8000/docs>
- ReDoc: <http://127.0.0.1:8000/redoc>
- OpenAPI JSON: <http://127.0.0.1:8000/openapi.json>
- Metrics: <http://127.0.0.1:8000/metrics>

Set the API key for your shell. PowerShell:

```powershell
$env:SERP_API_KEY = "sk_live_..."
```

Command Prompt:

```bat
set SERP_API_KEY=sk_live_...
```

Git Bash:

```bash
export SERP_API_KEY="sk_live_..."
```

Example search from PowerShell (using `Invoke-RestMethod` avoids native-quote escaping problems):

```powershell
$body = @{ query = "wireless headphones"; limit = 5 } | ConvertTo-Json
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/v1/search `
  -Method Post `
  -Headers @{ "X-API-Key" = $env:SERP_API_KEY } `
  -ContentType "application/json" `
  -Body $body
```

From Git Bash:

```bash
curl -sS http://127.0.0.1:8000/v1/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"wireless headphones","limit":5}'
```

## 9. Production Run on Windows

Production means running the same native services with production configuration and a Windows process supervisor. No Docker, VPS, domain, TLS, or reverse-proxy configuration is included. PostgreSQL and Redis already run as Windows services on this PC; keep them managed and backed up.

Production environment example (`.env`):

```dotenv
ENVIRONMENT=production
DEBUG=false
LOG_JSON=true
LOG_LEVEL=INFO
SECRET_KEY=replace-with-a-long-random-value
API_KEY_PEPPER=replace-with-a-long-random-value
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@DB_HOST:5432/DB_NAME
REDIS_URL=redis://:PASSWORD@REDIS_HOST:6379/0
CACHE_BACKEND=redis
ALLOWED_HOSTS=api.example.internal
CORS_ORIGINS=https://app.example.internal
SSRF_ALLOW_PRIVATE=false
METRICS_ENABLED=true
SENTRY_DSN=
```

Replace every development credential. Keep `API_KEY_PEPPER` stable for the lifetime of existing API keys.

Apply migrations before starting or restarting the new API version:

```powershell
alembic upgrade head
alembic current
```

Start API processes (single line; do not use `--reload`):

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers
```

Start one or more of each worker as separate supervised processes:

```powershell
arq app.workers.search_worker.WorkerSettings
arq app.workers.extraction_worker.WorkerSettings
arq app.workers.browser_worker.WorkerSettings
```

For an actual Windows service supervisor, wrap each command with NSSM, for example:

```powershell
nssm install SerpApi ".\.venv\Scripts\python.exe" "-m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers"
nssm set SerpApi AppDirectory "C:\Users\Bayezid\Desktop\Rat_Hunter_SERP"
nssm set SerpApi AppEnvironmentExtra "ENVIRONMENT=production"
nssm start SerpApi
```

Repeat for each ARQ worker using its own service name and command. Task Scheduler or Windows Service Control Manager is an alternative. No service files are included.

Production rules:

- Do not use `--reload`.
- Run PostgreSQL and Redis as managed Windows services with backups, authentication, and firewall isolation.
- Run workers as separate processes; do not embed them in the API process.
- Use a real process supervisor with restart-on-failure and graceful shutdown.
- Use `CACHE_BACKEND=redis`; memory mode is not shared across processes.
- Set explicit `ALLOWED_HOSTS` and `CORS_ORIGINS`.
- Keep `SSRF_ALLOW_PRIVATE=false` for internet-facing extraction.
- Replace default database/Redis credentials and rotate leaked keys immediately.
- Restrict NTFS permissions on `.env` to the service account, or inject secrets from a secret manager.
- Do not expose PostgreSQL or Redis to untrusted networks; allow only local/private firewall rules.
- Terminate TLS outside this project if the API is shared beyond localhost.
- Monitor `/v1/health`, `/v1/ready`, `/metrics`, process restarts, queue depth, database connections, and Redis health.
- Back up PostgreSQL and test restore procedures.

### Graceful Shutdown

Stop the API and workers with the supervisor's normal stop command, `Ctrl+C` in a development console, or `Stop-Service <name>` for a Windows service. Uvicorn and ARQ handle shutdown; the worker runtime closes the registry, browser pool, HTTP client, cache, and database engine.

Do not terminate browser worker processes with `Stop-Process -Force` or Task Manager "End task" unless they are hung. Chromium and Playwright child processes may otherwise be left behind.

## 10. Process Layout

Minimum local layout (four PowerShell windows, each with `.venv` activated):

```text
terminal 1: uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
terminal 2: arq app.workers.search_worker.WorkerSettings
terminal 3: arq app.workers.extraction_worker.WorkerSettings
terminal 4: arq app.workers.browser_worker.WorkerSettings
```

Minimum production layout:

```text
Windows service: postgresql-x64-18
Windows service: Redis
api process(es)
search worker process(es)
extraction worker process(es)
browser worker process(es)
external Windows process supervisor
```

## 11. Tests and Quality Checks

Run with `.venv` active:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
python -m mypy app
python -m compileall -q app migrations tests scripts
```

Optional real Redis integration tests (`redis-cli` from this PC works without a password):

```powershell
$env:SERP_TEST_REDIS_URL = "redis://localhost:6379/15"
python -m pytest tests/integration/test_redis_integration.py -q
```

Implementation details are in `docs/TESTING.md`. Performance testing is described in `docs/PERFORMANCE.md`.

## 12. Troubleshooting

Start with PowerShell:

```powershell
curl.exe -i http://127.0.0.1:8000/v1/health
curl.exe -i http://127.0.0.1:8000/v1/ready
alembic current
& "C:\Program Files\Redis\redis-cli.exe" ping
```

Common Windows causes:

- `Unable to copy ... venvlauncher.exe`: you ran `python -m venv .venv` while `.venv` was active, so Windows could not overwrite the running `python.exe`. Run `deactivate`, delete `.venv`, and recreate it with the base interpreter `C:\Users\Bayezid\AppData\Local\Python\pythoncore-3.14-64\python.exe`.
- `bash: .venvScriptsactivate: command not found`: you used backslashes in Git Bash. Use `source .venv/Scripts/activate`.
- `Activate.ps1 cannot be loaded because running scripts is disabled`: run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`, then activate again.
- `psql` / `redis-cli` is not recognized: they are not on `PATH`. Use the full paths shown above or add their folders to `PATH`.
- `database_unavailable`: PostgreSQL service is stopped, the `serp` password is wrong, or migrations have not run.
- `redis_unavailable`: Redis service is stopped or `REDIS_URL` is wrong.
- `401/403`: missing, invalid, expired, or revoked API key; or changed `API_KEY_PEPPER`.
- `503 worker_unavailable`: API cannot enqueue because Redis is unavailable.
- Browser failures: Chromium is not installed (`python -m playwright install chromium`), or the browser worker has insufficient memory.
- `ssrf_blocked`: the target is private/local/unsupported. Do not enable `SSRF_ALLOW_PRIVATE` on an internet-facing service.
- Port already in use:

  ```powershell
  netstat -ano | findstr :8000
  Stop-Process -Id <PID> -Force
  ```

Full diagnostics: `docs/TROUBLESHOOTING.md`.

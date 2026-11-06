# Database Schema

PostgreSQL is the production database. SQLAlchemy async sessions use `asyncpg`. Alembic owns schema changes. Tests use temporary SQLite databases via `aiosqlite` and create tables from the SQLAlchemy metadata.

## Relationship Overview

```text
accounts
  |-- applications
  |-- api_keys
  |-- search_history
  |-- extraction_jobs
  |-- usage_logs

applications
  |-- api_keys (optional)

api_keys
  |-- extraction_jobs (optional)
  |-- usage_logs (optional)

indexed_documents     (operator-managed internal search corpus)
search_sources        (source metadata)
source_health         (health snapshots)
system_settings       (operator key/value settings)
```

`accounts` is the tenancy boundary. Deleting an account cascades to applications, API keys, search history, jobs, and usage logs. Deleting an application or API key sets optional references to null where applicable.

## Shared Conventions

Most tables use:

- `id`: UUID primary key
- `created_at`: timezone-aware timestamp
- `updated_at`: timezone-aware timestamp

`source_health` is append-only and uses `checked_at` instead of audit timestamps.

## Tables

### `accounts`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `name` | varchar(200) | not null |
| `email` | varchar(320) | nullable, unique |
| `plan` | varchar(50) | not null, default `free` |
| `is_active` | boolean | not null, default true |
| `daily_quota` | integer | nullable |
| `rate_limit_per_minute` | integer | nullable |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Relationships: applications, API keys.

### `applications`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `account_id` | UUID | FK `accounts.id`, cascade delete |
| `name` | varchar(200) | not null |
| `description` | varchar(1000) | nullable |
| `is_active` | boolean | not null, default true |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Index: `account_id`.

### `api_keys`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `account_id` | UUID | FK `accounts.id`, cascade delete |
| `application_id` | UUID | FK `applications.id`, set null |
| `name` | varchar(200) | not null |
| `key_hash` | varchar(64) | not null, unique, indexed |
| `key_prefix` | varchar(24) | not null |
| `is_active` | boolean | not null, default true |
| `revoked_at` | timestamptz | nullable |
| `last_used_at` | timestamptz | nullable |
| `expires_at` | timestamptz | nullable |
| `rate_limit_per_minute` | integer | nullable |
| `daily_quota` | integer | nullable |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Indexes: `account_id`, `application_id`, `key_hash`. Only the HMAC hash and a short display prefix are stored.

### `search_history`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `account_id` | UUID | FK `accounts.id`, cascade delete |
| `request_id` | varchar(64) | nullable, indexed |
| `query` | varchar(512) | not null |
| `search_type` | varchar(32) | not null |
| `mode` | varchar(16) | not null |
| `country` | varchar(2) | not null |
| `language` | varchar(16) | not null |
| `result_count` | integer | not null |
| `processing_time_ms` | float | not null |
| `cached` | boolean | not null |
| `sources` | JSON | not null |
| `source_statuses` | JSON | not null |
| `error_code` | varchar(64) | nullable |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Indexes: `account_id`, `request_id`. Search-history writes are best effort and do not fail a search response.

### `extraction_jobs`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `account_id` | UUID | FK `accounts.id`, cascade delete |
| `api_key_id` | UUID | FK `api_keys.id`, set null |
| `job_type` | varchar(32) | not null (`search` or `extraction`) |
| `status` | varchar(16) | not null, default `queued`, indexed |
| `request_id` | varchar(64) | nullable |
| `payload` | JSON | not null |
| `result` | JSON | nullable |
| `error` | text | nullable |
| `attempts` | integer | not null, default 0 |
| `started_at` | timestamptz | nullable |
| `finished_at` | timestamptz | nullable |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Indexes: `account_id`, `status`.

### `search_sources`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `name` | varchar(80) | not null, unique, indexed |
| `adapter_type` | varchar(40) | not null |
| `enabled` | boolean | not null, default true |
| `description` | varchar(500) | nullable |
| `config` | JSON | not null |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Seeded by `scripts/seed.py`. Runtime adapter availability is controlled by environment settings, not only this table.

### `source_health`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `source_name` | varchar(80) | not null, indexed |
| `status` | varchar(24) | not null |
| `latency_ms` | float | nullable |
| `error` | varchar(1000) | nullable |
| `checked_at` | timestamptz | not null, indexed |

Written by `/v1/sources/health` when Redis is the cache backend. Rows are append-only and can be pruned by an operator.

### `usage_logs`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `account_id` | UUID | FK `accounts.id`, cascade delete |
| `api_key_id` | UUID | FK `api_keys.id`, set null |
| `endpoint` | varchar(120) | not null, indexed |
| `request_id` | varchar(64) | nullable, indexed |
| `status_code` | integer | not null |
| `processing_time_ms` | float | not null |
| `units` | integer | not null, default 1 |
| `cached` | boolean | not null |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Indexes: `account_id`, `endpoint`, `request_id`. Daily quota checks count rows for the account since UTC midnight.

### `system_settings`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `key` | varchar(120) | not null, unique, indexed |
| `value` | JSON | nullable |
| `description` | varchar(500) | nullable |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Available for operator-managed settings. Core runtime configuration remains environment based.

### `indexed_documents`

| Column | Type | Constraints |
| --- | --- | --- |
| `id` | UUID | primary key |
| `url` | varchar(2048) | not null, unique, indexed |
| `canonical_url` | varchar(2048) | nullable |
| `domain` | varchar(255) | not null, indexed |
| `title` | varchar(500) | not null |
| `content` | text | not null |
| `language` | varchar(16) | not null |
| `page_type` | varchar(32) | not null |
| `published_at` | timestamptz | nullable |
| `doc_metadata` | JSON | not null |
| `created_at` | timestamptz | not null |
| `updated_at` | timestamptz | not null |

Used by `CustomIndexAdapter`. It is not crawled automatically. Import documents with `scripts/seed.py`.

## Migration Workflow

```bash
alembic current
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

Run `alembic upgrade head` manually before starting the API and after deploying schema changes. See [../RUN_GUIDE.md](../RUN_GUIDE.md).

## Indexing and Performance Notes

- `api_keys.key_hash` is the authentication hot path.
- `usage_logs.account_id` supports quota checks; `created_at` is used in the daily filter.
- `search_history.request_id` and `usage_logs.request_id` support correlation.
- `indexed_documents` uses ILIKE token matching on title/content. Add PostgreSQL trigram or full-text indexes if the corpus grows substantially.
- `source_health` is append-only and should be pruned or partitioned at scale.
- JSON columns are used for flexible snapshots; frequently filtered JSON keys should be promoted to indexed columns.

## Test Database

`tests/conftest.py` creates a temporary SQLite file, creates the schema from metadata, and closes connections after each test module/session. Tests do not require PostgreSQL, Redis, Playwright, or network access for the default suite.

# API Documentation

Base path: `/v1`

Interactive documentation:

- Swagger UI: `GET /docs`
- ReDoc: `GET /redoc`
- OpenAPI 3 JSON: `GET /openapi.json`

## Authentication

Every endpoint except `/`, `/metrics`, `/v1/health`, and `/v1/ready` requires an API key.

```http
X-API-Key: sk_live_example_key
```

or:

```http
Authorization: Bearer sk_live_example_key
```

If both are present, `X-API-Key` is used. API keys are stored as HMAC-SHA256 hashes. The raw key is returned only once by `scripts/create_api_key.py`.

Authentication failures return `401 authentication_failed`. Revoked keys, disabled accounts, and disabled applications return `403 forbidden`. Expired or unknown keys return `401`.

## Request IDs

Send `X-Request-ID` to correlate logs and responses. If omitted, the API generates one. Valid IDs contain 1-128 characters from `A-Z`, `a-z`, `0-9`, `.`, `_`, `:`, or `-`. The response always includes the request ID header. Error payloads also include `request_id`.

## Error Format

All application errors use:

```json
{
  "success": false,
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [
      {
        "loc": ["body", "limit"],
        "msg": "Input should be less than or equal to 100",
        "type": "less_than_equal"
      }
    ]
  },
  "request_id": "2f8b7a9c3e5d4f10"
}
```

`details` is optional. The main error codes are:

| Code | Typical status | Meaning |
| --- | --- | --- |
| `invalid_query` | 422 | Search query rejected |
| `invalid_url` | 422 | URL syntax is invalid |
| `ssrf_blocked` | 400 | URL, credentials, port, or resolved address is blocked |
| `authentication_failed` | 401 | Missing, invalid, or expired API key |
| `forbidden` | 403 | Key, account, or application disabled |
| `rate_limit_exceeded` | 429 | Per-minute rate limit exceeded |
| `quota_exceeded` | 429 | Daily quota exceeded |
| `search_source_unavailable` | 503 | No usable search source |
| `search_timeout` | 504 | Search source timed out |
| `captcha_detected` | 503 | Upstream challenge page was detected; no bypass is attempted |
| `access_denied` | 403 | Upstream denied access |
| `extraction_failed` | 502 | Page fetch or extraction failed |
| `database_unavailable` | 503 | Authentication/persistence backend unavailable |
| `redis_unavailable` | 503 | Required Redis backend unavailable |
| `service_unavailable` | 503 | Browser or concurrency capacity unavailable |
| `not_found` | 404 | Resource does not exist for this account |
| `payload_too_large` | 413 | Body exceeds `MAX_REQUEST_BODY_BYTES` |
| `validation_error` | 422 | Pydantic/FastAPI request validation failed |
| `internal_error` | 500 | Unexpected server error |

## Rate Limits and Quotas

Limits are evaluated per API key. A key-level limit overrides the account limit, which overrides `DEFAULT_RATE_LIMIT_PER_MINUTE`. The quota lookup order is key, account, then `DEFAULT_DAILY_QUOTA`.

A rate-limit error includes:

```json
{
  "success": false,
  "error": {
    "code": "rate_limit_exceeded",
    "message": "Rate limit exceeded",
    "details": {
      "limit": 120,
      "used": 120,
      "remaining": 0,
      "reset_after_seconds": 17.4
    }
  },
  "request_id": "req-123"
}
```

## Search

### `POST /v1/search`

Synchronous search by default. Set `"async": true` to enqueue a background job.

Request fields:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `query` | string | required | 1-512 characters; whitespace is normalized |
| `country` | string | `"US"` | ISO 3166-1 alpha-2 |
| `language` | string | `"en"` | BCP-47-like code |
| `search_type` | enum | `"web"` | `web`, `products`, `news`, `images` |
| `mode` | enum | `"quick"` | `quick` or `deep` |
| `limit` | integer | `10` | 1-100 |
| `page` | integer | `1` | 1-10 |
| `safe_search` | boolean | `true` | Passed to supporting adapters |
| `include_domains` | string[] | `[]` | Up to 20 registrable domains |
| `exclude_domains` | string[] | `[]` | Up to 20 registrable domains |
| `sources` | string[] or null | `null` | Adapter names or adapter types; up to 20 |
| `use_cache` | boolean | `true` | Cache-aside lookup |
| `force_refresh` | boolean | `false` | Bypass cache and refresh |
| `async` | boolean | `false` | Enqueue instead of blocking |
| `extract_limit` | integer or null | null | Deep mode extraction count, 0-20 |
| `timeout_ms` | integer or null | null | 500-60000 |

`include_domains` and `exclude_domains` cannot overlap. Sources that do not support the requested `search_type` are ignored.

Example:

```bash
curl -sS http://localhost:8000/v1/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "wireless headphones",
    "country": "US",
    "language": "en",
    "search_type": "web",
    "mode": "quick",
    "limit": 2
  }'
```

Illustrative response:

```json
{
  "success": true,
  "metadata": {
    "query": "wireless headphones",
    "country": "US",
    "language": "en",
    "search_type": "web",
    "mode": "quick",
    "total_results": 1,
    "processing_time_ms": 412.37,
    "cached": false,
    "stale": false,
    "page": 1,
    "limit": 2,
    "sources_used": ["google"],
    "source_statuses": [
      {
        "name": "google",
        "status": "ok",
        "latency_ms": 387.2,
        "result_count": 1,
        "error_code": null,
        "error": null,
        "retried": false
      }
    ],
    "request_id": "9e4d1f7a2b8c4d63"
  },
  "results": [
    {
      "position": 1,
      "title": "Example Wireless Headphones",
      "url": "https://example.com/headphones",
      "canonical_url": "https://example.com/headphones",
      "domain": "example.com",
      "snippet": "Illustrative search result.",
      "content_type": "webpage",
      "source": "google",
      "published_at": null,
      "thumbnail": null,
      "extraction_status": "not_requested",
      "snippet_price": null,
      "extraction": null,
      "score": null,
      "rank_factors": {}
    }
  ],
  "warnings": []
}
```

Deep mode may populate `results[].extraction` with an `ExtractionPayload` and set `extraction_status` to `success`, `partial`, `pending`, or `failed`.

Partial source failures are represented in `metadata.source_statuses`; the request succeeds if at least one adapter produced a usable response.

### `POST /v1/products/search`

Extends the search request with:

| Field | Type | Notes |
| --- | --- | --- |
| `min_price` | number or null | Inclusive minimum |
| `max_price` | number or null | Inclusive maximum |
| `currency` | string or null | 3-letter ISO code |

`search_type` is fixed to `products`; supplying another value fails validation. Results with an unknown price are retained. Results with a price are filtered by the requested bounds/currency. Positions are recalculated after filtering.

```bash
curl -sS http://localhost:8000/v1/products/search \
  -H "X-API-Key: $SERP_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "mechanical keyboard",
    "country": "BD",
    "min_price": 3000,
    "max_price": 15000,
    "currency": "BDT",
    "limit": 5
  }'
```

The response shape is the same as `/v1/search`. `price_source`, `verified`, and `verification_method` in `snippet_price` or extracted product prices indicate provenance.

### Asynchronous Search

```json
{
  "query": "mechanical keyboard",
  "async": true
}
```

Response: `202 Accepted`

```json
{
  "success": true,
  "job_id": "b4f8f9d8-4c47-4da0-bb1e-575a74279075",
  "status": "queued",
  "status_url": "/v1/jobs/b4f8f9d8-4c47-4da0-bb1e-575a74279075"
}
```

## Extraction

### `POST /v1/extract`

Request fields:

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `url` | string | required | HTTP/HTTPS; no credentials; max 2048 characters |
| `include_text` | boolean | `true` | Visible main text |
| `include_media` | boolean | `true` | Images and video metadata |
| `allow_browser` | boolean | `true` | Allow browser fallback |
| `use_cache` | boolean | `true` | Use extraction cache |
| `force_refresh` | boolean | `false` | Bypass cache |
| `timeout_ms` | integer or null | null | 500-60000 |
| `async` | boolean | `false` | Enqueue extraction |

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

Illustrative response (abbreviated):

```json
{
  "success": true,
  "requested_url": "https://example.com/product/widget",
  "data": {
    "requested_url": "https://example.com/product/widget",
    "final_url": "https://example.com/product/widget",
    "status": "success",
    "page": {
      "title": "Example Widget",
      "description": "Illustrative product page.",
      "canonical_url": "https://example.com/product/widget",
      "domain": "example.com",
      "language": "en",
      "page_type": "product",
      "main_text": "Example Widget. Illustrative visible page text.",
      "headings": [{"level": 1, "text": "Example Widget"}],
      "keywords": ["widget", "example"],
      "open_graph": {"og:title": "Example Widget"},
      "twitter": {},
      "images": ["https://example.com/widget.jpg"],
      "favicon": "https://example.com/favicon.ico",
      "published_at": null,
      "modified_at": null,
      "author": null,
      "publisher": null
    },
    "product": {
      "name": "Example Widget",
      "description": "Illustrative product page.",
      "brand": "Example",
      "sku": "WIDGET-1",
      "gtin": null,
      "mpn": null,
      "price": {
        "amount": 19.99,
        "currency": "USD",
        "original_amount": null,
        "discount_amount": null,
        "discount_percent": null,
        "price_source": "structured_data",
        "verified": true,
        "verification_method": "json_ld",
        "extracted_at": "2026-01-01T00:00:00Z"
      },
      "availability": "in_stock",
      "condition": "new",
      "variants": [],
      "images": ["https://example.com/widget.jpg"],
      "product_url": "https://example.com/product/widget",
      "seller": null,
      "rating": null,
      "review_count": null,
      "category": null
    },
    "article": null,
    "business": null,
    "media": [
      {
        "url": "https://example.com/widget.jpg",
        "video_url": null,
        "thumbnail": null,
        "media_type": "image",
        "width": null,
        "height": null,
        "alt": null
      }
    ],
    "structured_data": [],
    "provenance": [],
    "extraction_method": "json_ld",
    "browser_used": false,
    "extracted_at": "2026-01-01T00:00:00Z",
    "warnings": []
  },
  "processing_time_ms": 238.91,
  "cached": false,
  "stale": false,
  "request_id": "2d7f6e1059ab4c3f"
}
```

`status` is `success`, `partial`, or `failed`. A `partial` result may still contain usable metadata and warnings. `provenance` records field-level extraction method, source URL, confidence, and validation status.

### `POST /v1/extract/batch`

Uses the same options as single extraction, but accepts:

| Field | Type | Notes |
| --- | --- | --- |
| `urls` | string[] | 1-20 URLs |
| `async` | boolean | Use the extraction or browser worker based on `allow_browser` |

```json
{
  "urls": ["https://example.com/a", "https://example.com/b"],
  "include_text": true,
  "include_media": false,
  "allow_browser": false
}
```

Response:

```json
{
  "success": true,
  "results": [],
  "errors": [],
  "processing_time_ms": 300.12,
  "request_id": "f31a2d93aa674b19"
}
```

Each successful item is a complete `ExtractionPayload`. Failed items appear in `errors` with `code` and `message`. `success` is false when any item failed.

## Jobs

### `GET /v1/jobs/{job_id}`

Returns a job only when it belongs to the authenticated account.

```json
{
  "job_id": "b4f8f9d8-4c47-4da0-bb1e-575a74279075",
  "status": "succeeded",
  "job_type": "search",
  "created_at": "2026-01-01T00:00:00Z",
  "updated_at": "2026-01-01T00:00:05Z",
  "started_at": "2026-01-01T00:00:01Z",
  "finished_at": "2026-01-01T00:00:05Z",
  "attempts": 1,
  "result": {"success": true, "metadata": {}, "results": [], "warnings": []},
  "error": null
}
```

Statuses: `queued`, `running`, `succeeded`, `failed`, `cancelled`. Retryable application errors are retried up to `JOB_MAX_RETRIES`. The result shape is the same as the corresponding synchronous endpoint.

## Account

### `GET /v1/account`

```json
{
  "id": "66cb4a8a-3b4f-4f8f-90ed-70f72426f48d",
  "name": "Local Development",
  "email": "local@example.test",
  "plan": "free",
  "is_active": true,
  "application": null,
  "api_key": {
    "id": "5f961232-93e6-4e6b-8d3c-104de7c087fa",
    "name": "default",
    "key_prefix": "sk_live_9f2b",
    "is_active": true,
    "created_at": "2026-01-01T00:00:00Z",
    "last_used_at": "2026-01-01T00:10:00Z",
    "expires_at": null,
    "rate_limit_per_minute": null,
    "daily_quota": null
  }
}
```

### `GET /v1/account/usage`

Query parameter `days` is 1-365 and defaults to 30.

```json
{
  "account_id": "66cb4a8a-3b4f-4f8f-90ed-70f72426f48d",
  "period_start": "2026-01-01T00:00:00Z",
  "period_end": "2026-01-31T00:00:00Z",
  "stats": {
    "total_requests": 128,
    "error_requests": 2,
    "cached_requests": 31,
    "total_units": 128,
    "by_endpoint": [
      {
        "endpoint": "/v1/search",
        "requests": 100,
        "errors": 1,
        "average_latency_ms": 412.7
      }
    ],
    "daily": [
      {"date": "2026-01-01", "requests": 20, "errors": 0}
    ],
    "quota": 10000,
    "quota_remaining": 9872,
    "rate_limit_per_minute": 120
  }
}
```

Usage is recorded by the access-log middleware after authenticated requests.

## Sources

### `GET /v1/sources`

```json
[
  {
    "name": "google",
    "adapter_type": "google_browser",
    "enabled": true,
    "capabilities": ["web", "products", "news"],
    "concurrency_limit": 5,
    "requires_browser": true,
    "description": "Google organic results rendered by the persistent browser pool"
  }
]
```

Enabled adapters depend on configuration. The Google adapter supports `web`, `products`, and `news`. Direct website adapters support `web` and `products`. The internal index adapter supports `web`, `products`, and `news`. None of the built-in adapters currently declares `images`, so an image-only search returns a source-unavailable error.

### `GET /v1/sources/health`

Runs bounded, non-destructive health checks through the cache and stores results when Redis is active.

```json
{
  "sources": [
    {
      "name": "google",
      "status": "ok",
      "latency_ms": 182.4,
      "error": null,
      "checked_at": "2026-01-01T00:00:00Z"
    }
  ],
  "checked_at": "2026-01-01T00:00:00Z"
}
```

Statuses: `ok`, `degraded`, `unavailable`, `timeout`, `blocked`, `disabled`, `unknown`.

## Health

### `GET /v1/health`

Liveness only; does not require authentication.

```json
{
  "status": "ok",
  "version": "1.0.0",
  "environment": "production",
  "uptime_seconds": 143.2,
  "components": {
    "api": {"status": "ok", "detail": null}
  }
}
```

### `GET /v1/ready`

Checks PostgreSQL and the configured cache backend.

```json
{
  "ready": true,
  "checks": {"database": true, "cache": true}
}
```

Returns `503` with the same body shape when either check fails.

## Root and Metrics

- `GET /` returns the application name, version, documentation path, and health path.
- `GET /metrics` returns Prometheus text exposition when `METRICS_ENABLED=true`.

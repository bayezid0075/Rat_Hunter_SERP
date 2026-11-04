# MASTER DEVELOPMENT PROMPT
## Project: Universal SERP API
### Independent, Provider-Agnostic, High-Performance Search & Web Data Extraction Platform

---

# 1. YOUR ROLE

Act as a Principal Software Architect, Senior Python Backend Engineer, Search Infrastructure Engineer, Web Crawling Engineer, Performance Engineer, Database Architect, and QA Engineer.

Your responsibility is to design, implement, test, and document a complete production-grade Universal SERP API.

Do not create a simple scraper, prototype, demo, or tutorial project.

Build a real, modular, maintainable, secure, asynchronous, high-performance Search API inspired by the developer experience of Serper.dev.

The system must be independently developed and operated. It must not require any mandatory third-party commercial search API, AI provider, or proprietary extraction service.

IMPORTANT:
- Do not implement VPS deployment.
- Do not configure Nginx, production domains, SSL certificates, or cloud infrastructure.
- Docker and Docker Compose are required for local development and testing.
- Complete all application code, tests, documentation, schemas, and local infrastructure.

Do not stop after creating the project structure or initial files. Implement the complete working application.

---

# 2. PROJECT VISION

Build an independent Universal SERP API that allows any external application to search the web and retrieve structured information from multiple web pages.

Example applications:
- E-commerce price comparison platforms.
- Product discovery applications.
- Research platforms.
- Content discovery applications.
- Business directories.
- Web intelligence applications.
- Mobile applications.
- Third-party developer applications.

The system must expose a consistent REST API regardless of the underlying search source or extraction technology.

The project must not contain application-specific business logic.

Its primary responsibilities are:

1. Search Discovery.
2. SERP Collection.
3. Web Page Extraction.
4. Structured Data Extraction.
5. Result Normalization.
6. Duplicate Detection.
7. Search Ranking.
8. Caching.
9. API Access Management.
10. Usage Monitoring.

---

# 3. CORE ARCHITECTURAL PRINCIPLES

Follow these mandatory principles:

1. Provider-independent architecture.
2. Adapter-based search infrastructure.
3. Modular monolith for the initial release.
4. Separate API and background worker processes.
5. Async-first programming.
6. High concurrency with controlled resource consumption.
7. Redis-based caching and coordination.
8. PostgreSQL for persistent application data.
9. Universal extraction schemas.
10. Replaceable search and AI adapters.
11. Strict typing and Pydantic validation.
12. Comprehensive automated testing.
13. Security-first implementation.
14. Performance-first implementation.
15. Consistent API response contracts.

Never couple the Core Engine directly to a particular search provider.

Never make OpenAI, Gemini, Serper, SerpAPI, or another commercial service mandatory.

Search and extraction must continue to work without AI.

---

# 4. TARGET PERFORMANCE

Initial production target:

- API throughput: 10 requests per second.
- Hosting target: Ubuntu VPS.
- Recommended hardware: 4 vCPU, 8 GB RAM, 100 GB NVMe.
- Search freshness: Live search by default.
- Quick Search: target p95 below 3 seconds for successful live searches.
- Cache Hit: target p95 below 200 ms.
- Deep Search: target p95 below 5 seconds for ordinary responsive sources.
- API error rate target: below 1% under defined benchmark conditions.

These are engineering targets, not guaranteed source-independent SLAs.

Design the application so that the search concurrency and worker capacity can be configured without modifying application code.

Do not assume that accepting 10 API requests per second means every external search source can process 10 fresh searches per second.

Implement global and per-source concurrency controls.

---

# 5. REQUIRED TECHNOLOGY STACK

Use the following technology stack:

## Backend
- Python 3.13+
- FastAPI
- Uvicorn
- uvloop on supported Linux environments
- Pydantic v2
- HTTPX
- orjson

## Search & Browser
- Playwright
- Persistent browser workers
- BeautifulSoup4
- lxml
- JSON-LD parser
- Schema.org extraction

## Database
- PostgreSQL
- SQLAlchemy 2.x async
- Alembic migrations
- asyncpg

## Caching & Background Processing
- Redis
- ARQ or an equivalent lightweight Redis-backed async job system
- Redis connection pooling

Choose one background job framework and implement it consistently. Do not create multiple competing queue systems.

## Testing
- pytest
- pytest-asyncio
- httpx AsyncClient
- k6
- coverage

## Code Quality
- Ruff
- mypy or pyright
- pre-commit

Pin compatible dependency versions in the project dependency configuration and provide reproducible installation instructions.

---

# 6. SYSTEM ARCHITECTURE

Implement the following logical architecture:

Client Applications
        |
        v
Nginx-compatible HTTP interface (application must not configure Nginx)
        |
        v
FastAPI API Gateway
        |
        v
Authentication & Request Validation
        |
        v
Search Orchestrator
        |
        +-----------------------+
        |                       |
        v                       v
Search Adapter Manager     Cache Manager
        |
        +-----------------------+
        |           |           |
        v           v           v
   Search A      Search B    Own Index
        |
        v
Result Aggregator
        |
        v
Universal Extraction Engine
        |
        +-----------------------+
        |           |           |
        v           v           v
     HTTPX      Playwright    Structured
     Parser     Extractor     Data Parser
        |
        v
Normalization & Deduplication
        |
        v
Ranking Engine
        |
        v
Unified JSON Response

Supporting infrastructure:
- Redis
- PostgreSQL
- Background workers
- Browser workers
- Metrics and structured logging

---

# 7. SEARCH ADAPTER SYSTEM

This is one of the most important parts of the project.

Implement a formal SearchAdapter interface.

Example conceptual interface:

class SearchAdapter:
    async def search(self, query, options):
        raise NotImplementedError

    async def health_check(self):
        raise NotImplementedError

Each adapter must:
- Accept a standardized search request.
- Return a standardized SearchResult collection.
- Support timeout configuration.
- Report source-specific errors.
- Support health monitoring.
- Respect source-specific concurrency limits.
- Be independently enabled or disabled.
- Be replaceable without modifying the orchestrator.

Implement:

## A. Google Browser Search Adapter

Use Playwright to collect Google SERP results where automated access is permitted.

Requirements:
- Persistent browser lifecycle.
- Browser resource pooling.
- Configurable locale and region.
- Search result extraction.
- Organic result collection.
- Pagination support where appropriate.
- CAPTCHA and access-block detection.
- Respect applicable access restrictions.
- Never attempt CAPTCHA bypass.
- Do not continuously retry blocked requests.

Do not assume Google's HTML structure is permanent. Keep selectors isolated, configurable, and testable.

## B. Direct Website Search Adapter

Implement a generic website-search adapter interface.

It must support website-specific search implementations through plugins.

Examples of possible supported website integrations:
- Daraz
- Pickaboo
- Star Tech
- Gadget & Gear
- Other configurable websites.

Do not hardcode all website logic inside the main search engine.

Each website integration must be independently configurable and replaceable.

## C. Custom Search / Crawler Adapter

Create an adapter interface for searching the platform's own indexed documents.

Do not claim that this is a full Internet search engine.

Prepare the architecture for future crawler and index integration.

## D. Future Adapter Compatibility

The architecture must allow future search adapters to be added without modifying:
- API routes.
- Core schemas.
- Extraction engine.
- Result aggregator.
- Database structure unnecessarily.

Do not create fake adapters that return hardcoded results.

---

# 8. SEARCH ORCHESTRATOR

Create a central SearchOrchestrator.

Responsibilities:

1. Validate the incoming query.
2. Normalize search parameters.
3. Identify enabled search adapters.
4. Check cache policy.
5. Apply global concurrency limits.
6. Apply per-source concurrency limits.
7. Execute eligible searches concurrently.
8. Handle timeouts independently.
9. Collect successful results.
10. Normalize results.
11. Remove duplicates.
12. Rank results.
13. Return a consistent response.

Use asyncio and controlled concurrent execution.

Use asyncio.gather with return_exceptions=True or an equivalent safe pattern.

One adapter failure must not crash the complete search request.

Implement:
- Per-source timeouts.
- Global request deadlines.
- Circuit breakers.
- Bounded retries.
- Failure classification.
- Partial results.
- Source health tracking.

Never retry CAPTCHA, access-denied, or permanent validation errors aggressively.

---

# 9. UNIVERSAL EXTRACTION ENGINE

Build a generic extraction system that can process different kinds of web pages.

The engine must identify and extract:

## General Page Data
- Title
- Description
- Canonical URL
- Domain
- Language
- Page type
- Main text
- Headings
- Keywords
- Open Graph data
- Twitter metadata

## Product Data
- Product name
- Description
- Brand
- SKU
- GTIN
- MPN
- Price
- Original price, if explicitly available
- Currency
- Availability
- Product condition
- Variants
- Images
- Product URL
- Seller information, if available

## Article Data
- Title
- Author
- Publisher
- Publication date
- Modified date
- Description
- Article text
- Main image

## Business Data
- Business name
- Address
- Telephone
- Website
- Category
- Geographic metadata, when published

## Media Data
- Image URL
- Image alt text
- Video URL
- Thumbnail
- Media type

---

# 10. EXTRACTION PRIORITY

Implement the following extraction order:

1. JSON-LD.
2. Schema.org microdata.
3. RDFa where practical.
4. Open Graph metadata.
5. Standard HTML metadata.
6. HTML headings and content.
7. Website-specific extraction plugins.
8. Optional AI-based fallback.

AI must never be required for basic extraction.

For every extracted field, maintain provenance where possible:

- field name
- extracted value
- extraction method
- source URL
- extraction timestamp
- confidence level
- validation status

Do not fabricate missing values.

Unknown values must be null or omitted according to the response schema.

Do not mark a price as verified merely because it appears in a search snippet.

Distinguish between:
- Search snippet price.
- Structured page price.
- Page-visible price.
- Current availability.
- Verified product offer.

Do not claim a value is current unless the verification process supports that claim.

---

# 11. EXTRACTION ENGINE PERFORMANCE

Implement a two-stage extraction system.

## Stage A: Fast HTTP Extraction

Use HTTPX.

- Connection pooling.
- Async requests.
- Configurable timeout.
- Response-size limits.
- Redirect validation.
- HTML content detection.
- Compression support.
- Streaming where appropriate.

Parse using lxml.

## Stage B: Browser Extraction

Use Playwright only when necessary.

Examples:
- JavaScript-rendered content.
- Missing essential product information.
- Dynamic page content.
- Client-rendered structured data.

Requirements:
- Persistent browser processes.
- Controlled page concurrency.
- Configurable navigation timeout.
- Browser health checks.
- Page cleanup.
- Context isolation.
- Memory monitoring.
- Crash recovery.

Do not launch a new browser for every incoming request.

---

# 12. UNIVERSAL DATA SCHEMAS

Create standardized Pydantic v2 schemas.

Required schema groups:

1. SearchRequest
2. SearchResponse
3. SearchResult
4. ExtractionRequest
5. ExtractionResponse
6. ExtractedField
7. ProductData
8. ArticleData
9. BusinessData
10. MediaData
11. SearchMetadata
12. SourceStatus
13. ErrorResponse
14. JobStatus

SearchResult should include:

- position
- title
- url
- canonical_url
- domain
- snippet
- content_type
- source
- published_at
- thumbnail
- extraction_status

SearchMetadata should include:

- query
- country
- language
- search_type
- total_results
- processing_time_ms
- cached
- stale
- sources_used
- source_statuses
- request_id

Every API response must follow a consistent contract.

Use strict validation and type-safe models.

---

# 13. REST API ENDPOINTS

Implement versioned REST APIs.

Required endpoints:

POST /v1/search

General web search.

POST /v1/extract

Extract a single URL.

POST /v1/extract/batch

Extract multiple URLs.

POST /v1/products/search

Product-focused search.

GET /v1/jobs/{job_id}

Background job status.

GET /v1/health

Application health.

GET /v1/ready

Readiness check.

GET /v1/account/usage

API usage statistics.

GET /v1/sources

Available and configured search sources.

GET /v1/sources/health

Search source health status.

GET /v1/account

Current API account information.

Implement appropriate HTTP status codes.

Generate OpenAPI documentation automatically.

---

# 14. QUICK SEARCH AND DEEP SEARCH

Implement two search modes.

## QUICK SEARCH

Purpose: Lowest possible latency.

Return:
- SERP position
- Title
- URL
- Domain
- Snippet
- Thumbnail, when available
- Source
- Basic classification

Do not perform unnecessary full-page extraction.

## DEEP SEARCH

Purpose: More complete information.

Return:
- SERP results
- Page description
- Product information
- Price
- Currency
- Brand
- Availability
- Images
- Structured data
- Extraction provenance

Run page extraction concurrently with strict resource limits.

If full extraction cannot finish before the request deadline, support an asynchronous job workflow.

The client must be able to retrieve the completed result using a job ID.

---

# 15. CACHING ARCHITECTURE

Implement Redis caching.

Cache categories:

- Search SERP results.
- Extracted page metadata.
- Product extraction.
- Website metadata.
- Source health information.

Initial configurable TTL:

General SERP: 30–60 seconds.
Product SERP: 15–30 seconds.
Extracted page: 1–5 minutes.
Website metadata: 1 hour.

Use cache keys containing:

- Normalized query.
- Country.
- Language.
- Search type.
- Result limit.
- Relevant search configuration.

Implement:

1. Cache-aside.
2. Stale-while-revalidate.
3. Cache bypass.
4. Cache invalidation.
5. Cache stampede prevention.
6. Request coalescing.
7. Redis TTL management.

Do not cache user-specific responses across different API accounts when their permissions or configuration differ.

Expose cache information in API metadata.

---

# 16. CONCURRENCY MANAGEMENT

Create a dedicated concurrency management module.

Initial configuration:

- API processes: configurable, initially 4.
- Global Search concurrency: 20.
- HTTP extraction concurrency: 20.
- Browser instances: 2.
- Browser pages: 2–4.
- Database connections: configurable, initially 10–20 total.
- HTTP connection pool: configurable.

Important:

A local asyncio semaphore is process-local.

If multiple API processes or worker processes exist, implement global limits through Redis or another shared coordination mechanism.

Never rely on independent per-process limits as a global system limit.

Implement:
- Per-adapter concurrency.
- Per-domain concurrency.
- Global concurrency.
- Queue limits.
- Backpressure.
- Request deadlines.
- Resource exhaustion protection.

All limits must be configurable through environment variables.

---

# 17. DATABASE DESIGN

Use PostgreSQL with SQLAlchemy 2.x async.

Implement Alembic migrations.

Required tables:

- applications
- api_keys
- accounts
- search_history
- extraction_jobs
- search_sources
- source_health
- usage_logs
- system_settings

Requirements:

- UUID primary keys.
- Proper foreign keys.
- Indexes for frequently queried fields.
- Created and updated timestamps.
- API key hashes rather than plaintext storage.
- Query optimization.
- Database connection pooling.
- Transaction management.
- Pagination for history endpoints.

Do not permanently store every raw SERP result unless explicitly configured.

Use Redis for temporary search data.

Avoid storing sensitive request information unnecessarily.

---

# 18. API AUTHENTICATION AND SECURITY

Implement API-key-based authentication.

Requirements:

- Secure random API key generation.
- Store hashed API keys.
- Support key revocation.
- Per-key request limits.
- Per-key usage tracking.
- Configurable quotas.
- Authentication middleware or dependency.
- Proper 401 and 403 responses.
- Request validation.
- Input sanitization.
- Structured security logging.

## SSRF PROTECTION — CRITICAL

The extraction API accepts URLs, so SSRF protection is mandatory.

Implement:
- Only HTTP and HTTPS.
- Reject localhost.
- Reject private IP ranges.
- Reject loopback addresses.
- Reject link-local addresses.
- Reject cloud metadata endpoints.
- Validate DNS resolution.
- Revalidate every redirect.
- Protect against DNS rebinding.
- Limit redirect count.
- Block unsupported ports.
- Restrict response size.
- Apply connection and read timeouts.
- Restrict internal network access at the container/network level.

Do not rely on hostname string matching alone.

Browser navigation must follow equivalent restrictions.

Do not expose cookies, authorization headers, or internal credentials to untrusted websites.

---

# 19. ERROR HANDLING

Create a centralized exception system.

Classify errors:

- Invalid query.
- Invalid URL.
- Authentication failure.
- Rate limit exceeded.
- Search source unavailable.
- Search timeout.
- CAPTCHA detected.
- Access denied.
- Extraction failed.
- Invalid structured data.
- Database unavailable.
- Redis unavailable.
- Worker unavailable.
- Internal server error.

All errors must have a consistent JSON response.

Do not leak stack traces, internal paths, secrets, or credentials to API clients.

Implement structured internal logs with request IDs.

---

# 20. RESULT NORMALIZATION AND DEDUPLICATION

Create reusable processors.

## URL Normalization

- Remove tracking parameters where safe.
- Normalize trailing slashes.
- Normalize hostname casing.
- Resolve canonical URLs.
- Handle redirects safely.
- Preserve meaningful query parameters.

## Duplicate Detection

Compare:
- Canonical URL.
- Normalized URL.
- Domain.
- Product identifiers.
- Title similarity where appropriate.

Do not merge different product variants merely because their titles look similar.

## Price Normalization

Represent:
- Amount.
- Currency.
- Original amount.
- Discount information.
- Price source.
- Extraction timestamp.

Never compare prices from different currencies without a defined conversion policy.

Never invent discount values.

---

# 21. SEARCH RANKING

Create an independent ranking module.

Initial ranking factors:

- Search position.
- Title relevance.
- Query similarity.
- Domain relevance.
- Content type.
- Source reliability.
- Freshness, when available.

Keep ranking weights configurable.

Do not embed ranking logic inside individual adapters.

The same SearchResult should be rankable regardless of its source.

Do not assume that a product's lowest price is necessarily the most relevant result.

---

# 22. BACKGROUND WORKERS

Implement separate worker entry points.

Required worker categories:

1. Search worker.
2. Extraction worker.
3. Browser worker.

Use Redis-backed job processing.

Requirements:

- Retry with bounded backoff.
- Job timeout.
- Job status.
- Failed job tracking.
- Queue monitoring.
- Worker health.
- Graceful shutdown.
- Idempotent processing where appropriate.
- Duplicate job prevention.

Do not force Quick Search requests into a background queue when they can finish safely within the synchronous request deadline.

---

# 23. MONITORING AND OBSERVABILITY

Implement:

- Structured JSON logging.
- Request IDs.
- Prometheus-compatible metrics.
- Search latency.
- Extraction latency.
- Adapter success rate.
- Adapter failure rate.
- Cache hit rate.
- Redis health.
- Database health.
- Queue depth.
- Browser worker health.
- Active browser pages.
- CPU and memory metrics where available.

Create an initial Grafana dashboard definition if practical.

Support Sentry integration as an optional configuration.

The application must still start when optional Sentry configuration is absent.

---

# 24. PROJECT STRUCTURE

Use this modular structure as the starting point. You may improve it if necessary, but preserve clear separation of concerns.

universal-serp-api/
│
├── app/
│   ├── main.py
│   ├── config.py
│   │
│   ├── api/
│   │   ├── routes/
│   │   ├── dependencies.py
│   │   └── middleware.py
│   │
│   ├── core/
│   │   ├── orchestrator.py
│   │   ├── concurrency.py
│   │   ├── cache.py
│   │   ├── security.py
│   │   ├── exceptions.py
│   │   └── logging.py
│   │
│   ├── adapters/
│   │   ├── search/
│   │   └── ai/
│   │
│   ├── extractors/
│   │   ├── base.py
│   │   ├── http.py
│   │   ├── browser.py
│   │   ├── jsonld.py
│   │   ├── metadata.py
│   │   └── schema.py
│   │
│   ├── processors/
│   │   ├── normalize.py
│   │   ├── deduplicate.py
│   │   ├── ranking.py
│   │   └── price.py
│   │
│   ├── services/
│   ├── workers/
│   ├── database/
│   ├── schemas/
│   ├── models/
│   └── monitoring/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── performance/
│   └── security/
│
├── migrations/
├── scripts/
├── deployment/
│   └── docker/
│
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── .gitignore
└── README.md

---

# 25. AUTOMATED TESTING REQUIREMENTS

Create meaningful automated tests.

## Unit Tests

Test:
- Query normalization.
- URL normalization.
- Schema validation.
- JSON-LD parsing.
- Metadata extraction.
- Price parsing.
- Currency handling.
- Deduplication.
- Ranking.
- Cache keys.
- Error mapping.

## Integration Tests

Test:
- API authentication.
- Search orchestration.
- Adapter failure.
- Redis integration.
- PostgreSQL integration.
- Extraction endpoint.
- Background jobs.
- Browser worker lifecycle.

Use local fixture pages and mocked network services.

Do not make the normal test suite depend on live Google access or arbitrary external websites.

## Security Tests

Test:
- SSRF attempts.
- Private IP addresses.
- Redirect to internal addresses.
- Malformed URLs.
- Oversized responses.
- Authentication bypass.
- Rate-limit bypass.
- Invalid payloads.

## Performance Tests

Use k6.

Test:
- 10 RPS sustained load.
- 30-minute load.
- Cache hit performance.
- Live search performance.
- Extraction performance.
- Source timeout behavior.
- Worker saturation.
- Memory growth.
- Resource cleanup.

Report p50, p95, p99 latency, throughput, error rate, and resource utilization.

---

# 26. LOCAL DEVELOPMENT ENVIRONMENT

Provide a complete Docker Compose environment containing:

- FastAPI API.
- PostgreSQL.
- Redis.
- Search worker.
- Extraction worker.
- Browser worker.

Requirements:

- Health checks.
- Persistent database volume.
- Environment-based configuration.
- Service dependencies.
- Graceful shutdown.
- Development-friendly logs.

Do not include production domain or VPS deployment configurations.

Provide:
- .env.example
- Installation instructions.
- Database migration commands.
- Development commands.
- Test commands.
- API documentation instructions.

---

# 27. DOCUMENTATION

Create comprehensive documentation.

Required:

1. README.md
2. ARCHITECTURE.md
3. API_DOCUMENTATION.md
4. DEVELOPMENT.md
5. SEARCH_ADAPTER_GUIDE.md
6. EXTRACTION_GUIDE.md
7. DATABASE_SCHEMA.md
8. SECURITY.md
9. PERFORMANCE.md
10. TESTING.md
11. TROUBLESHOOTING.md

Document every public endpoint with request and response examples.

Explain how to create a new Search Adapter without modifying the Core Engine.

Explain how to create a website-specific extraction plugin.

---

# 28. IMPLEMENTATION RULES

Follow these rules strictly:

1. Inspect the repository before modifying anything.
2. If the repository already contains code, preserve useful existing functionality.
3. Do not overwrite existing work without inspection.
4. Establish a clear architecture before implementation.
5. Implement in logical phases.
6. Write real working code.
7. Do not leave critical TODO placeholders.
8. Do not use fake search results.
9. Do not hardcode API secrets.
10. Do not fabricate prices or extracted values.
11. Do not silently swallow exceptions.
12. Avoid unnecessary dependencies.
13. Avoid circular imports.
14. Keep functions small and testable.
15. Use type hints.
16. Use dependency injection where appropriate.
17. Add tests alongside implementation.
18. Run tests after each major phase.
19. Fix errors before moving forward.
20. Keep documentation synchronized with the actual implementation.

If an external search source cannot be accessed in the current environment, implement its real adapter interface, safe error handling, fixture-based tests, and document the access limitation. Never claim that live integration has been verified when it has not.

Do not attempt to bypass CAPTCHA, paywalls, authentication, or website access restrictions.

---

# 29. EXECUTION STRATEGY

Work through these phases in order.

PHASE 1: Repository inspection and architecture setup.

PHASE 2: Project foundation, configuration, schemas, database, and authentication.

PHASE 3: Search Adapter interface and Search Orchestrator.

PHASE 4: Search source integrations.

PHASE 5: Universal Extraction Engine.

PHASE 6: Normalization, deduplication, and ranking.

PHASE 7: Redis caching and concurrency management.

PHASE 8: Background workers and asynchronous jobs.

PHASE 9: API endpoints and complete OpenAPI documentation.

PHASE 10: Security hardening.

PHASE 11: Unit, integration, security, and performance tests.

PHASE 12: Documentation and final verification.

After every phase:

- Summarize implemented functionality.
- List created and modified files.
- Run relevant tests.
- Report failures.
- Fix failures.
- Update the implementation checklist.

Do not declare a phase complete merely because files were created.

---

# 30. FINAL ACCEPTANCE CRITERIA

The project is considered complete only when:

[ ] FastAPI application starts successfully.

[ ] PostgreSQL integration works.

[ ] Redis integration works.

[ ] Database migrations work.

[ ] API key authentication works.

[ ] Search Adapter architecture works.

[ ] Search Orchestrator works.

[ ] Configured search integrations have been tested to the extent access permits.

[ ] HTTP extraction works.

[ ] Playwright browser extraction works.

[ ] JSON-LD extraction works.

[ ] Metadata extraction works.

[ ] Product extraction works.

[ ] Article extraction works.

[ ] Universal response schemas work.

[ ] URL normalization works.

[ ] Deduplication works.

[ ] Redis caching works.

[ ] Global concurrency controls work across processes.

[ ] Background workers work.

[ ] Async job status works.

[ ] SSRF protection tests pass.

[ ] API rate limiting works.

[ ] Error responses are standardized.

[ ] OpenAPI documentation is generated.

[ ] Unit tests pass.

[ ] Integration tests pass.

[ ] Security tests pass.

[ ] k6 performance tests are implemented and executable.

[ ] Docker Compose local development works.

[ ] Documentation is complete.

[ ] No mandatory external commercial search provider exists.

[ ] No mandatory AI provider exists.

[ ] No hardcoded fake production results exist.

[ ] No critical implementation TODOs remain.

---

# FINAL INSTRUCTION

Start by inspecting the existing repository.

Then produce a concise implementation plan and architecture decisions.

After that, immediately begin implementing Phase 1.

Continue through all phases systematically.

Do not stop after the planning stage.

Do not ask me to approve every file or every minor technical decision.

Make sensible engineering decisions independently and document them.

If a genuinely blocking decision is required, ask one concise question.

Your goal is to deliver a complete, secure, tested, maintainable, provider-independent Universal SERP API that can be used by multiple independent applications through a stable REST API.

Remember: Build a real Search Infrastructure, not merely a Google scraper.
# Search Adapter Guide

Search adapters isolate source-specific discovery logic from orchestration. Add an adapter without changing routes, schemas, ranking, cache policy, or the core response contract.

## Choose the Right Extension Point

Use a `WebsiteSearchPlugin` when:

- the source is an HTML search results page
- results can be parsed from cards or links
- the shared SSRF-safe HTTP fetcher is sufficient

Use a full `SearchAdapter` when:

- the source uses a non-HTML protocol
- it needs a browser, SDK, database, or a custom authentication flow
- it has source-specific pagination, result types, or health behavior

Built-in direct-site plugins are implemented with the first approach. Google and the internal index use the second.

## Contract

Every adapter implements `app.adapters.search.base.SearchAdapter`:

```python
class SearchAdapter(ABC):
    name: str
    adapter_type: str = "unknown"
    capabilities: tuple[SearchType, ...] = (SearchType.WEB,)
    requires_browser: bool = False
    description: str = ""
    concurrency_limit: int = 5

    def supports(self, search_type: SearchType) -> bool:
        return search_type in self.capabilities

    @abstractmethod
    async def search(self, options: SearchOptions) -> list[SearchResult]: ...

    @abstractmethod
    async def health_check(self) -> AdapterHealth: ...

    async def close(self) -> None: ...
```

Return only standardized `SearchResult` objects. Do not return raw HTML, provider-specific dictionaries, or fabricated results.

## Option 1: Add a Direct Website Plugin

Declare selectors in a `ConfigurablePlugin` subclass:

```python
from app.adapters.search.plugins.base import ConfigurablePlugin


class ExampleShopPlugin(ConfigurablePlugin):
    name = "exampleshop"
    display_name = "Example Shop"
    base_url = "https://shop.example"
    search_path = "/search?q={query}"
    currency_hint = "USD"
    card_selectors = (".product-card", ".search-result")
    title_selectors = (".product-title", "h3", "a")
    price_selectors = (".price", "[class*='price']")
    image_selectors = ("img",)
```

Register it in `app/adapters/search/plugins/__init__.py`:

```python
def build_plugins(settings: Settings) -> list[WebsiteSearchPlugin]:
    plugins: list[WebsiteSearchPlugin] = []
    if settings.exampleshop_enabled:
        plugins.append(ExampleShopPlugin())
    return plugins
```

Add the corresponding settings to `app/config.py` and `.env.example`, for example `EXAMPLESHOP_ENABLED=false`. Then run `scripts/seed.py` if you want source metadata for operators.

The generated adapter name is `direct_exampleshop`; its `adapter_type` is `direct_website`. Either can be passed in a request's `sources` array.

The base plugin:

- builds a URL from `base_url` and `search_path`
- uses `quote_plus` for the query
- fetches through `HttpFetcher`
- detects CAPTCHA and access-denied pages without bypassing them
- parses cards, then falls back to product-like links
- parses prices through `build_price` with `SEARCH_SNIPPET` provenance

Test the parser with stored HTML fixtures. Do not require live network access in the normal suite.

## Option 2: Add a Full Adapter

Create `app/adapters/search/example.py`:

```python
from __future__ import annotations

import time

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.config import Settings
from app.schemas.common import SearchType, SourceHealthStatus
from app.schemas.search import SearchResult


class ExampleAdapter(SearchAdapter):
    name = "example"
    adapter_type = "example_api"
    capabilities = (SearchType.WEB, SearchType.NEWS)
    description = "Example source integration"
    concurrency_limit = 5

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def search(self, options: SearchOptions) -> list[SearchResult]:
        if not options.query.strip():
            return []
        # Perform a real, bounded request here.
        # Map results to SearchResult and set source="example".
        return []

    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        try:
            # Perform a cheap, non-destructive check.
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.OK,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except Exception as exc:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.UNAVAILABLE,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=exc.__class__.__name__,
            )
```

Register it in `app/adapters/search/registry.py` inside `build_registry`:

```python
if settings.example_enabled:
    registry.register(ExampleAdapter(settings))
```

Add `example_enabled: bool = False` to `Settings` and `EXAMPLE_ENABLED=false` to `.env.example`. Keep the adapter disabled by default unless its access terms and egress requirements are understood.

## Required Result Fields

At minimum, set:

- `position`: 1-based result position
- `title`: non-empty title
- `url`: absolute HTTP/HTTPS URL
- `source`: adapter name
- `content_type`: best available classification

Recommended fields:

- `canonical_url`
- `domain`
- `snippet`
- `published_at`
- `thumbnail`
- `snippet_price` with `PriceSource.SEARCH_SNIPPET`

Do not set extraction fields from search snippets. Deep extraction is owned by the orchestrator and `ExtractionService`.

## Error Handling

Raise `AppError` subclasses from `app.core.exceptions`:

- `CaptchaDetectedError` for challenge pages
- `AccessDeniedError` for explicit access denial
- `SourceTimeoutError` for timeouts
- `SourceUnavailableError` for upstream outages or invalid upstream responses
- `InvalidQueryError` when the adapter cannot accept a query

The orchestrator records per-source statuses and keeps partial results. Unexpected exceptions are logged and represented as unavailable sources.

Never bypass CAPTCHA, paywalls, authentication, or access restrictions.

## Timeouts and Concurrency

- Respect `SearchOptions.timeout_seconds`.
- Respect `options.limit` and `options.page`.
- Do not spawn unbounded background tasks or requests.
- Use shared `HttpFetcher` or `BrowserPool` when possible.
- Keep `concurrency_limit` realistic; the orchestrator also applies per-adapter concurrency.

## Health Checks

Health checks must be cheap, bounded, and non-destructive. Return:

- `OK` when usable
- `DEGRADED` when partial functionality exists
- `BLOCKED` for CAPTCHA/access denial
- `UNAVAILABLE` for network/upstream failure
- `TIMEOUT` when the check exceeds its bound

`/v1/sources/health` calls every registered adapter concurrently with `ADAPTER_TIMEOUT`.

## Testing

Add tests under `tests/unit/` for parsing and under `tests/integration/` when orchestration changes.

Use `tests.support.StubHttpFetcher` for HTTP-level tests:

```python
from app.adapters.search.example import ExampleAdapter
from app.config import Settings
from tests.support import StubHttpFetcher


async def test_example_adapter() -> None:
    adapter = ExampleAdapter(Settings(environment="test"))
    # Inject or stub the transport used by the adapter.
    # Assert normalized SearchResult fields, not raw HTML.
```

Cover:

- normal results
- empty results
- malformed cards
- relative URLs
- duplicate URLs
- CAPTCHA/access-denied detection
- timeout/unavailable errors
- source filtering by `name` and `adapter_type`

Run:

```bash
python -m pytest tests/unit -q
python -m mypy app
python -m ruff check .
```

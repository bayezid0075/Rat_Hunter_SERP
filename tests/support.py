"""Shared fixtures, fakes and HTML fixtures for tests."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.config import Settings
from app.core.cache import CacheManager
from app.core.security import hash_api_key
from app.database.session import Database
from app.extractors.http import FetchResult
from app.models.account import Account
from app.models.api_key import ApiKey
from app.models.application import Application
from app.schemas.common import ContentType, SearchType, SourceHealthStatus
from app.schemas.search import SearchResult


class StubHttpFetcher:
    def __init__(
        self,
        *,
        html: str = "",
        final_url: str | None = None,
        error: Exception | None = None,
    ) -> None:
        self.html = html
        self.final_url = final_url
        self.error = error
        self.calls: list[str] = []
        self.closed = False

    async def fetch(self, url: str, **_: Any) -> FetchResult:
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        return FetchResult(
            requested_url=url,
            final_url=self.final_url or url,
            status_code=200,
            text=self.html,
            headers={"content-type": "text/html; charset=utf-8"},
            content_type="text/html",
            elapsed_ms=1.0,
            encoding="utf-8",
        )

    async def aclose(self) -> None:
        self.closed = True


class FakeSearchAdapter(SearchAdapter):
    name = "fake"
    adapter_type = "fake"
    capabilities = (SearchType.WEB, SearchType.PRODUCTS, SearchType.NEWS)
    description = "Deterministic test adapter"

    def __init__(self) -> None:
        self.results: list[SearchResult] = []
        self.error: Exception | None = None
        self.calls = 0
        self.health_status = SourceHealthStatus.OK

    async def search(self, options: SearchOptions) -> list[SearchResult]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [result.model_copy(deep=True) for result in self.results[: options.limit]]

    async def health_check(self) -> AdapterHealth:
        return AdapterHealth(name=self.name, status=self.health_status, latency_ms=1.0)


class FakeArqPool:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, Any]] = []
        self.closed = False

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> Any:
        self.enqueued.append({"function": function, "args": args, "kwargs": kwargs})
        return type("EnqueuedJob", (), {"job_id": kwargs.get("_job_id")})()

    async def aclose(self) -> None:
        self.closed = True


@dataclass(slots=True)
class Harness:
    app: Any
    settings: Settings
    db: Database
    cache: CacheManager
    adapter: FakeSearchAdapter
    http: StubHttpFetcher
    arq: FakeArqPool
    account_id: UUID
    api_key_id: UUID
    application_id: UUID
    raw_key: str
    client: Any
    extra: dict[str, Any] = field(default_factory=dict)


async def create_api_key(
    db: Database,
    settings: Settings,
    *,
    name: str = "test key",
    account_name: str = "Test Account",
    rate_limit: int | None = None,
    daily_quota: int | None = None,
    expires_at: datetime | None = None,
    is_active: bool = True,
    account_is_active: bool = True,
    with_application: bool = True,
    raw_key: str | None = None,
) -> tuple[Account, Application | None, ApiKey, str]:
    raw = raw_key or f"sk_test_{secrets.token_urlsafe(24)}"
    async with db.session_factory() as session:
        account = Account(name=account_name, is_active=account_is_active)
        session.add(account)
        await session.flush()
        application = None
        if with_application:
            application = Application(account_id=account.id, name="test app")
            session.add(application)
            await session.flush()
        api_key = ApiKey(
            account_id=account.id,
            application_id=application.id if application else None,
            name=name,
            key_hash=hash_api_key(raw, settings.api_key_pepper),
            key_prefix=raw[:16],
            expires_at=expires_at,
            is_active=is_active,
            rate_limit_per_minute=rate_limit,
            daily_quota=daily_quota,
        )
        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)
        return account, application, api_key, raw


def sample_results() -> list[SearchResult]:
    return [
        SearchResult(
            position=1,
            title="Best laptops in Bangladesh",
            url="https://example.com/products/laptop",
            canonical_url="https://example.com/products/laptop",
            domain="example.com",
            snippet="A great laptop priced at BDT 55,000.",
            content_type=ContentType.PRODUCT,
            source="fake",
        ),
        SearchResult(
            position=2,
            title="Laptop buying guide",
            url="https://example.org/guides/laptop",
            canonical_url="https://example.org/guides/laptop",
            domain="example.org",
            snippet="How to choose a laptop.",
            content_type=ContentType.ARTICLE,
            source="fake",
        ),
        SearchResult(
            position=3,
            title="Example laptop store",
            url="https://shop.example.net/laptop",
            canonical_url="https://shop.example.net/laptop",
            domain="shop.example.net",
            snippet="Laptops and accessories.",
            content_type=ContentType.WEBPAGE,
            source="fake",
        ),
    ]


PRODUCT_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Acme UltraBook 14</title>
  <meta name="description" content="A lightweight laptop from Acme.">
  <meta property="og:title" content="Acme UltraBook 14">
  <meta property="og:image" content="https://example.com/images/ultrabook.jpg">
  <link rel="canonical" href="https://example.com/products/acme-ultrabook-14">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Acme UltraBook 14",
    "description": "A lightweight 14 inch laptop.",
    "brand": {"@type": "Brand", "name": "Acme"},
    "sku": "ACME-UB14",
    "gtin13": "1234567890123",
    "image": ["https://example.com/images/ultrabook.jpg"],
    "offers": {
      "@type": "Offer",
      "price": "1299.00",
      "priceCurrency": "USD",
      "availability": "https://schema.org/InStock",
      "seller": {"@type": "Organization", "name": "Acme Store"}
    },
    "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.6", "reviewCount": "120"}
  }
  </script>
</head>
<body>
  <main>
    <h1>Acme UltraBook 14</h1>
    <p>A lightweight 14 inch laptop for work and travel.</p>
    <p>Price: $1,299.00</p>
  </main>
</body>
</html>
"""

ARTICLE_HTML = """
<!doctype html>
<html lang="en">
<head>
  <title>How laptops are changing</title>
  <meta property="og:type" content="article">
  <meta property="article:published_time" content="2026-01-02T10:00:00Z">
  <script type="application/ld+json">
  {
    "@context": "https://schema.org",
    "@type": "NewsArticle",
    "headline": "How laptops are changing",
    "description": "A look at modern laptop design.",
    "articleBody": "Modern laptops are thinner and faster than ever before.",
    "datePublished": "2026-01-02T10:00:00Z",
    "author": {"@type": "Person", "name": "Jane Doe"},
    "publisher": {"@type": "Organization", "name": "Example News"}
  }
  </script>
</head>
<body>
<article><h1>How laptops are changing</h1><p>Modern laptops are thinner and faster than ever before.</p></article>
</body>
</html>
"""

DARAZ_HTML = """
<div class="gridItem--Yd0sa">
  <a href="/products/acme-laptop-i-123.html">
    <img src="/images/laptop.jpg">
    <div class="RfADt"><a href="/products/acme-laptop-i-123.html">Acme Laptop 14</a></div>
    <div class="aBrP0">৳ 55,000</div>
  </a>
</div>
<div class="gridItem--Yd0sa">
  <a href="/products/other-phone-i-456.html">
    <img data-src="/images/phone.jpg">
    <div class="RfADt"><a href="/products/other-phone-i-456.html">Other Phone 5G</a></div>
    <div class="aBrP0">৳ 32,500</div>
  </a>
</div>
"""

GOOGLE_HTML = """
<div class="g">
  <a href="https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Flaptops&sa=U">
    <h3>Laptops at Example</h3>
  </a>
  <div class="VwiC3b">Find the best laptop deals from BDT 50,000.</div>
  <img src="/images/laptop.jpg">
</div>
<div class="g">
  <a href="https://example.org/reviews"><h3>Laptop reviews</h3></a>
  <div class="VwiC3b">Independent laptop reviews and buying advice.</div>
</div>
"""

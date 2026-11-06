from __future__ import annotations

from datetime import UTC, datetime

from app.processors.deduplicate import deduplicate_results
from app.processors.normalize import (
    apply_domain_filters,
    canonical_url,
    classify_content_type,
    domain_allowed,
    domain_from_url,
    normalize_query,
    normalize_url,
    query_tokens,
)
from app.processors.price import build_price, compare_prices, detect_currency, parse_price
from app.processors.ranking import RankingEngine
from app.schemas.common import ContentType, SearchType
from app.schemas.product import PriceSource
from app.schemas.search import SearchResult


def _result(url: str, *, title: str = "Example title", position: int = 1, **kwargs: object) -> SearchResult:
    return SearchResult(
        position=position,
        title=title,
        url=url,
        canonical_url=url,
        domain=domain_from_url(url),
        source="fake",
        **kwargs,
    )


def test_normalize_helpers() -> None:
    assert normalize_query("  best   laptops \n") == "best laptops"
    assert query_tokens("Best Laptop 2026!") == {"best", "laptop", "2026"}
    assert (
        normalize_url("HTTPS://WWW.Example.COM:443/a/?utm_source=x&b=2&a=1#frag") == "https://www.example.com/a?a=1&b=2"
    )
    assert normalize_url("https://example.com/a/b/") == "https://example.com/a/b"
    assert domain_from_url("https://www.example.com/x") == "example.com"
    assert canonical_url("https://example.com/x?utm_source=a", "https://example.com/canonical") == (
        "https://example.com/canonical"
    )


def test_classify_content_type() -> None:
    assert classify_content_type("https://example.com/products/a") is ContentType.PRODUCT
    assert classify_content_type("https://example.com/news/a") is ContentType.ARTICLE
    assert classify_content_type("https://example.com/store/a") is ContentType.BUSINESS
    assert classify_content_type("https://example.com/about") is ContentType.WEBPAGE


def test_domain_filters() -> None:
    assert domain_allowed("shop.example.com", ["example.com"], [])
    assert not domain_allowed("example.com", [], ["example.com"])
    assert not domain_allowed("other.com", ["example.com"], [])
    results = [
        _result("https://example.com/a"),
        _result("https://other.com/b"),
        _result("https://sub.example.com/c"),
    ]
    filtered = apply_domain_filters(results, ["example.com"], [])
    assert [result.domain for result in filtered] == ["example.com", "sub.example.com"]


def test_price_parsing_and_currency_detection() -> None:
    assert detect_currency("Only BDT here") == "BDT"
    assert detect_currency("100 Taka") == "BDT"
    assert detect_currency("no price") is None

    assert parse_price("BDT 55,000").amount == 55_000
    assert parse_price("BDT 55,000").currency == "BDT"
    assert parse_price("$1,299.00").amount == 1_299.0
    assert parse_price("$1,299.00").currency == "USD"
    assert parse_price("৳ 32,500").currency == "BDT"
    assert parse_price("€1.234,56").amount == 1_234.56
    assert parse_price("999", default_currency="USD").currency == "USD"
    assert parse_price("free shipping") is None

    price = build_price("$1,299.00", source=PriceSource.PAGE_VISIBLE)
    assert price is not None
    assert price.amount == 1_299.0
    assert price.currency == "USD"
    assert price.original_amount is None

    discounted = build_price("$80", source=PriceSource.PAGE_VISIBLE, original_text="$100")
    assert discounted is not None
    assert discounted.discount_amount == 20.0
    assert discounted.discount_percent == 20.0

    assert compare_prices(price, discounted) == 1
    assert compare_prices(price, None) is None


def test_deduplicate_prefers_higher_quality() -> None:
    first = _result("https://example.com/product/?utm_source=a", title="Same", position=2)
    second = _result("https://example.com/product", title="Same", position=1, score=0.8)
    deduped = deduplicate_results([first, second])
    assert len(deduped) == 1
    assert deduped[0].url == "https://example.com/product"


def test_ranking_rewards_relevance_and_reassigns_positions() -> None:
    relevant = _result("https://example.com/laptop", title="Best laptop deals", position=2)
    irrelevant = _result("https://example.com/phone", title="Phone accessories", position=1)
    ranked = RankingEngine().rank([irrelevant, relevant], "laptop", search_type=SearchType.WEB)
    assert ranked[0].title == "Best laptop deals"
    assert [result.position for result in ranked] == [1, 2]
    assert ranked[0].score is not None and ranked[0].score > (ranked[1].score or 0)


def test_ranking_handles_missing_freshness() -> None:
    old = _result("https://example.com/old", title="laptop old", position=1)
    old.published_at = datetime(2020, 1, 1, tzinfo=UTC)
    new = _result("https://example.com/new", title="laptop new", position=2)
    new.published_at = datetime.now(UTC)
    ranked = RankingEngine().rank([old, new], "laptop")
    assert ranked[0].rank_factors["freshness"] > ranked[1].rank_factors["freshness"]

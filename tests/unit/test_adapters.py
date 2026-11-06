from __future__ import annotations

import pytest
from app.adapters.search.base import SearchOptions
from app.adapters.search.direct import DirectWebsiteAdapter
from app.adapters.search.google import (
    _unwrap_google_url,
    detect_google_block,
    parse_google_results,
)
from app.adapters.search.plugins.sites import DarazPlugin
from app.config import Settings
from app.core.exceptions import CaptchaDetectedError
from app.schemas.common import SearchType
from tests.support import DARAZ_HTML, GOOGLE_HTML, StubHttpFetcher


def test_parse_google_results() -> None:
    results = parse_google_results(
        GOOGLE_HTML,
        "https://www.google.com/search?q=laptops",
        limit=10,
    )
    assert len(results) == 2
    assert results[0].title == "Laptops at Example"
    assert results[0].url == "https://example.com/laptops"
    assert results[0].domain == "example.com"
    assert results[0].snippet
    assert results[0].thumbnail == "https://www.google.com/images/laptop.jpg"
    assert results[0].snippet_price is not None
    assert results[0].snippet_price.currency == "BDT"
    assert results[0].snippet_price.amount == 50_000
    assert results[1].url == "https://example.org/reviews"


def test_google_url_unwrapping() -> None:
    assert (
        _unwrap_google_url("https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fa&sa=U") == "https://example.com/a"
    )
    assert _unwrap_google_url("https://example.com/direct") == "https://example.com/direct"
    assert _unwrap_google_url("https://www.google.com/maps") is None
    assert _unwrap_google_url("javascript:alert(1)") is None
    assert _unwrap_google_url("https://evilgoogle.com/url?q=https://example.com") == (
        "https://evilgoogle.com/url?q=https://example.com"
    )


def test_google_block_detection() -> None:
    with pytest.raises(CaptchaDetectedError):
        detect_google_block("https://www.google.com/sorry/index", "<html></html>", {})
    with pytest.raises(CaptchaDetectedError):
        detect_google_block(
            "https://www.google.com/search",
            "<html>unusual traffic from your network</html>",
            {},
        )


async def test_direct_adapter_fetches_and_parses() -> None:
    settings = Settings(environment="test")
    fetcher = StubHttpFetcher(html=DARAZ_HTML, final_url="https://www.daraz.com.bd/catalog/?q=laptop")
    adapter = DirectWebsiteAdapter(plugin=DarazPlugin(), settings=settings, http_fetcher=fetcher)
    results = await adapter.search(
        SearchOptions(
            query="laptop",
            search_type=SearchType.PRODUCTS,
            limit=1,
            timeout_seconds=2,
        )
    )
    assert len(results) == 1
    assert results[0].source == "direct_daraz"
    assert fetcher.calls
    assert "laptop" in fetcher.calls[0]

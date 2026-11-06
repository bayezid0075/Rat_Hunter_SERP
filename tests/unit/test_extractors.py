from __future__ import annotations

import pytest
from app.adapters.search.plugins.base import detect_block
from app.adapters.search.plugins.sites import DarazPlugin
from app.config import Settings
from app.core.exceptions import AccessDeniedError, CaptchaDetectedError
from app.extractors.base import Document, ProvenanceCollector
from app.extractors.heuristics import merge_products
from app.extractors.jsonld import product_from_item
from app.extractors.metadata import extract_metadata
from app.extractors.pipeline import ExtractionPipeline
from app.schemas.common import ContentType, ExtractionStatus
from app.schemas.product import Availability, PriceData, ProductData, SellerData
from tests.support import ARTICLE_HTML, DARAZ_HTML, PRODUCT_HTML


@pytest.fixture
def pipeline() -> ExtractionPipeline:
    return ExtractionPipeline(Settings(environment="test"))


async def test_product_pipeline_uses_json_ld(pipeline: ExtractionPipeline) -> None:
    payload = await pipeline.extract_from_html(
        PRODUCT_HTML,
        "https://example.com/products/acme-ultrabook-14",
        include_text=True,
        include_media=True,
    )
    assert payload.status is ExtractionStatus.SUCCESS
    assert payload.extraction_method == "json_ld"
    assert payload.page.title == "Acme UltraBook 14"
    assert payload.page.page_type is ContentType.PRODUCT
    assert payload.product is not None
    assert payload.product.name == "Acme UltraBook 14"
    assert payload.product.brand == "Acme"
    assert payload.product.sku == "ACME-UB14"
    assert payload.product.gtin == "1234567890123"
    assert payload.product.price is not None
    assert payload.product.price.amount == 1299.0
    assert payload.product.price.currency == "USD"
    assert payload.product.availability is Availability.IN_STOCK
    assert payload.product.rating == 4.6
    assert payload.product.review_count == 120
    assert payload.structured_data
    assert payload.provenance


async def test_article_pipeline(pipeline: ExtractionPipeline) -> None:
    payload = await pipeline.extract_from_html(ARTICLE_HTML, "https://example.org/news/laptops")
    assert payload.status is ExtractionStatus.SUCCESS
    assert payload.page.page_type is ContentType.ARTICLE
    assert payload.article is not None
    assert payload.article.title == "How laptops are changing"
    assert payload.article.author == "Jane Doe"
    assert payload.article.publisher == "Example News"
    assert payload.article.published_at is not None


async def test_empty_page_is_partial(pipeline: ExtractionPipeline) -> None:
    payload = await pipeline.extract_from_html(
        "<html><body><script type='application/ld+json'>{not json}</script></body></html>",
        "https://example.com/empty",
    )
    assert payload.status is ExtractionStatus.PARTIAL
    assert payload.page.title is None
    assert payload.product is None
    assert payload.warnings


async def test_metadata_extraction() -> None:
    document = Document(html=PRODUCT_HTML, url="https://example.com/products/acme-ultrabook-14")
    metadata = extract_metadata(document, ProvenanceCollector(document.url))
    assert metadata.page.title == "Acme UltraBook 14"
    assert metadata.page.canonical_url == "https://example.com/products/acme-ultrabook-14"
    assert metadata.open_graph["og:title"] == "Acme UltraBook 14"
    assert metadata.open_graph["og:image"] == "https://example.com/images/ultrabook.jpg"
    assert metadata.page.main_text
    assert "lightweight" in metadata.page.main_text.casefold()


def test_json_ld_handles_non_mapping_offers() -> None:
    product = product_from_item(
        {
            "@type": "Product",
            "name": "Test",
            "offers": "not-an-offer",
        }
    )
    assert product is not None
    assert product.name == "Test"
    assert product.price is None
    assert product.availability is Availability.UNKNOWN


def test_merge_products_handles_unhashable_nested_values() -> None:
    primary = ProductData(name="Test")
    fallback = ProductData(
        price=PriceData(amount=10, currency="USD"),
        seller=SellerData(name="Shop"),
        variants=[{"color": "black"}],
        images=["https://example.com/image.jpg"],
    )
    merged = merge_products(primary, fallback)
    assert merged is not None
    assert merged.price is not None
    assert merged.price.amount == 10
    assert merged.seller is not None
    assert merged.seller.name == "Shop"
    assert merged.variants == [{"color": "black"}]
    assert merged.images == ["https://example.com/image.jpg"]


def test_daraz_plugin_parses_cards() -> None:
    plugin = DarazPlugin()
    results = plugin.parse(
        DARAZ_HTML,
        "https://www.daraz.com.bd/catalog/?q=laptop",
        limit=10,
        adapter_name="direct_daraz",
    )
    assert len(results) == 2
    assert results[0].title == "Acme Laptop 14"
    assert results[0].url == "https://www.daraz.com.bd/products/acme-laptop-i-123.html"
    assert results[0].thumbnail == "https://www.daraz.com.bd/images/laptop.jpg"
    assert results[0].snippet_price is not None
    assert results[0].snippet_price.amount == 55_000
    assert results[0].snippet_price.currency == "BDT"
    assert results[1].thumbnail == "https://www.daraz.com.bd/images/phone.jpg"


def test_detect_block_markers() -> None:
    with pytest.raises(CaptchaDetectedError):
        detect_block("<html>Please complete the captcha to continue</html>")
    with pytest.raises(AccessDeniedError):
        detect_block("<html>Access denied</html>")
    detect_block("<html><body>Normal page</body></html>")

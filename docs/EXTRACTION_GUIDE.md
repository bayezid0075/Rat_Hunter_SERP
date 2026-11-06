# Extraction Guide

The extraction subsystem turns a fetched HTML page into a normalized, provenance-aware payload. It supports products, articles, local businesses, media, text, structured data, and optional site-specific plugins.

## Two-Stage Fetching

`ExtractionService.extract_url` first uses the SSRF-safe HTTP fetcher. Browser rendering is attempted only when all of these are true:

- `allow_browser=true` in the request
- `BROWSER_FALLBACK=true` in configuration
- the static payload appears insufficient

Insufficient means:

- a product is present but has no price
- there is no main text and no structured data
- both title and main text are missing

The static and browser payloads are scored. Browser data replaces the static payload only when it is richer. Browser failure adds a warning and preserves the static result.

## Pipeline Priority

`ExtractionPipeline.extract_from_html` applies this order:

1. JSON-LD
2. Microdata
3. RDFa
4. Open Graph / Twitter / HTML metadata
5. Conservative HTML heuristics
6. Domain-specific extraction plugin
7. Optional AI fallback

Structured data wins. Lower-priority sources fill only empty or placeholder values. Fields are merged individually, not by replacing a whole product/article/business object.

Price values are never silently merged across sources. Each `PriceData` records:

- `price_source`: `search_snippet`, `structured_data`, `page_visible`, or `verified_offer`
- `verified`
- `verification_method`
- `extracted_at`

## Output

`ExtractionPayload` includes:

- `requested_url`, `final_url`, `status`
- `page`: title, description, canonical URL, domain, language, page type, main text, headings, keywords, Open Graph/Twitter maps, images, favicon, dates, author, publisher
- `product`, `article`, `business` when detected
- `media`
- `structured_data`: bounded raw JSON-LD, microdata, and RDFa items
- `provenance`: field-level extraction records
- `extraction_method`
- `browser_used`
- `warnings`

Extraction status is:

- `success` when usable content was found
- `partial` when the page lacked a title or other required content
- `failed` when no usable content remains

## Provenance

`ProvenanceCollector` records:

```json
{
  "field": "product.price",
  "value": 19.99,
  "method": "json_ld",
  "source_url": "https://example.com/product",
  "extracted_at": "2026-01-01T00:00:00Z",
  "confidence": 0.9,
  "validation_status": "unverified"
}
```

Use provenance when a downstream consumer needs to know where a value came from or how much to trust it.

## Website-Specific Plugins

Plugins are matched by hostname after universal extraction. A plugin only runs for its declared domains and cannot affect unrelated sites.

Contract:

```python
class ExtractionPlugin(ABC):
    name: str
    domains: tuple[str, ...] = ()

    def matches(self, url: str) -> bool: ...

    @abstractmethod
    def extract(
        self,
        document: Document,
        metadata: MetadataResult,
        collector: ProvenanceCollector,
    ) -> PluginExtraction: ...
```

`PluginExtraction` can return a product, article, business, and additional provenance fields.

### Example Plugin

Create `app/extractors/plugins/example.py`:

```python
from __future__ import annotations

from bs4 import Tag

from app.extractors.base import Document, ProvenanceCollector, clean_text
from app.extractors.metadata import MetadataResult
from app.extractors.plugins.base import ExtractionPlugin, PluginExtraction
from app.processors.price import build_price
from app.schemas.common import ExtractionMethod
from app.schemas.product import PriceSource, ProductData


class ExampleExtractionPlugin(ExtractionPlugin):
    name = "example"
    domains = ("example.com",)

    def extract(
        self,
        document: Document,
        metadata: MetadataResult,
        collector: ProvenanceCollector,
    ) -> PluginExtraction:
        soup = document.soup
        name_tag = soup.select_one("[data-product-name]")
        price_tag = soup.select_one("[data-product-price]")

        name = clean_text(name_tag.get_text(" ", strip=True)) if isinstance(name_tag, Tag) else None
        price_text = clean_text(price_tag.get_text(" ", strip=True)) if isinstance(price_tag, Tag) else None
        price = (
            build_price(
                price_text,
                source=PriceSource.PAGE_VISIBLE,
                default_currency="USD",
                verification_method="example_plugin",
            )
            if price_text
            else None
        )

        if not name and price is None:
            return PluginExtraction()

        product = ProductData(
            name=name,
            price=price,
            product_url=metadata.page.canonical_url or document.url,
        )
        collector.add("product.name", name, ExtractionMethod.PLUGIN, confidence=0.9)
        collector.add("product.price", price.amount if price else None, ExtractionMethod.PLUGIN, confidence=0.9)
        return PluginExtraction(product=product)
```

Register it in `app/extractors/plugins/__init__.py`:

```python
def build_extraction_plugins() -> ExtractionPluginRegistry:
    registry = ExtractionPluginRegistry()
    registry.register(DarazExtractionPlugin())
    registry.register(ExampleExtractionPlugin())
    return registry
```

No core pipeline change is needed.

### Plugin Rules

- Do not mutate `document.soup`. Parsers may share the document; create a local copy if you need to remove nodes.
- Return only what is present. Do not invent missing prices, ratings, availability, or identifiers.
- Use `collector.add(...)` for provenance.
- Use `build_price(...)` instead of parsing monetary values by hand.
- Keep site knowledge in the plugin, not in the pipeline.
- Catch plugin failures in the pipeline boundary, not by silently returning fabricated defaults.
- Match domains exactly or as a subdomain; do not use substring matching.

## Optional AI Fallback

`ExtractionPipeline` accepts an optional `AIExtractor` implementing `app.adapters.ai.base.AIExtractor`. AI extraction is disabled unless explicitly supplied and `enabled` is true. AI-derived prices are marked unverified and use lower provenance confidence.

AI must remain optional. Core extraction and tests must not require an external AI service.

## Browser Pool Notes

- Browser rendering is expensive and consumes memory/shared memory.
- `BROWSER_INSTANCES` and `BROWSER_PAGES_PER_INSTANCE` bound capacity.
- The browser worker should run with adequate shared memory. On Linux, mount or allocate sufficient `/dev/shm`; the launch options also include `--disable-dev-shm-usage`.
- Browser pages must be closed after use; the pool handles lifecycle.
- Do not use the browser to bypass CAPTCHA, authentication, or paywalls.

## Limits

- Response body size: `HTTP_MAX_RESPONSE_BYTES`
- Redirects: `HTTP_MAX_REDIRECTS`
- Ports: `HTTP_ALLOWED_PORTS`
- Private addresses: blocked unless `SSRF_ALLOW_PRIVATE=true`
- Main text: `EXTRACTION_MAX_TEXT_CHARS`
- Batch: 1-20 URLs
- Per-URL timeout: 500-60000 ms
- Media items and structured data are intentionally bounded

## Testing

Use local fixtures from `tests/support.py` and the extraction pipeline directly:

```python
pipeline = ExtractionPipeline(Settings(environment="test"))
payload = await pipeline.extract_from_html(html, url)
assert payload.page.title == "Expected title"
```

Cover:

- JSON-LD product, article, and business
- malformed/non-mapping offers
- microdata and RDFa
- Open Graph and Twitter fallback
- visible-text heuristics
- non-HTML and oversized responses
- SSRF and redirects
- plugin match/no-match and missing fields
- browser fallback scoring with fakes

Run:

```bash
python -m pytest tests/unit/test_extractors.py tests/security/test_ssrf.py -q
python -m mypy app
```

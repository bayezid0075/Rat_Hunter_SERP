"""Universal extraction pipeline with deterministic priority ordering."""

from __future__ import annotations

from typing import Any

from app.adapters.ai.base import AIExtractor
from app.config import Settings
from app.core.logging import get_logger
from app.extractors.base import Document, ProvenanceCollector, clean_text
from app.extractors.heuristics import (
    any_product_value,
    extract_article_heuristic,
    extract_business_heuristic,
    extract_media,
    extract_product_heuristic,
    merge_articles,
    merge_businesses,
    merge_products,
)
from app.extractors.jsonld import extract_structured, iter_jsonld
from app.extractors.metadata import extract_metadata
from app.extractors.plugins.base import ExtractionPluginRegistry
from app.extractors.schema import extract_microdata_and_rdfa
from app.schemas.common import ContentType, ExtractionMethod, ExtractionStatus
from app.schemas.extraction import ExtractionPayload
from app.schemas.product import PriceData, PriceSource, ProductData

logger = get_logger(__name__)


class ExtractionPipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        plugin_registry: ExtractionPluginRegistry | None = None,
        ai_extractor: AIExtractor | None = None,
    ) -> None:
        self.settings = settings
        self.plugin_registry = plugin_registry
        self.ai_extractor = ai_extractor

    async def extract_from_html(
        self,
        html: str,
        url: str,
        *,
        include_text: bool = True,
        include_media: bool = True,
        browser_used: bool = False,
    ) -> ExtractionPayload:
        document = Document(html=html, url=url)
        collector = ProvenanceCollector(url)
        metadata = extract_metadata(
            document,
            collector,
            include_text=include_text,
            max_text_chars=self.settings.extraction_max_text_chars,
        )
        page = metadata.page

        jsonld_items = iter_jsonld(document.soup)
        (
            product_jsonld,
            article_jsonld,
            business_jsonld,
            _,
        ) = extract_structured(
            jsonld_items,
            source_url=url,
            collector=collector,
            method=ExtractionMethod.JSON_LD,
        )
        microdata_items, rdfa_items = extract_microdata_and_rdfa(document.soup)
        product_micro, article_micro, business_micro, _ = extract_structured(
            microdata_items,
            source_url=url,
            collector=collector,
            method=ExtractionMethod.MICRODATA,
        )
        product_rdfa, article_rdfa, business_rdfa, _ = extract_structured(
            rdfa_items,
            source_url=url,
            collector=collector,
            method=ExtractionMethod.RDFA,
        )

        product = merge_products(product_jsonld, product_micro)
        product = merge_products(product, product_rdfa)
        article = merge_articles(article_jsonld, article_micro)
        article = merge_articles(article, article_rdfa)
        business = merge_businesses(business_jsonld, business_micro)
        business = merge_businesses(business, business_rdfa)

        heuristic_product = extract_product_heuristic(document, metadata, collector)
        heuristic_article = extract_article_heuristic(document, metadata, collector)
        heuristic_business = extract_business_heuristic(document, metadata, collector)
        product = merge_products(product, heuristic_product)
        article = merge_articles(article, heuristic_article)
        business = merge_businesses(business, heuristic_business)

        plugin_used = False
        if self.plugin_registry is not None:
            plugin = self.plugin_registry.match(url)
            if plugin is not None:
                try:
                    plugin_result = plugin.extract(document, metadata, collector)
                except Exception:
                    logger.warning("extraction_plugin_failed", extra={"plugin": plugin.name}, exc_info=True)
                else:
                    if plugin_result.product is not None:
                        product = merge_products(plugin_result.product, product)
                        plugin_used = True
                    if plugin_result.article is not None:
                        article = merge_articles(plugin_result.article, article)
                        plugin_used = True
                    if plugin_result.business is not None:
                        business = merge_businesses(plugin_result.business, business)
                        plugin_used = True
                    collector.extend(plugin_result.fields)

        ai_used = False
        if self.ai_extractor is not None and self.ai_extractor.enabled:
            try:
                ai_values = await self.ai_extractor.extract_structured(
                    html,
                    url,
                    wanted=[
                        "name",
                        "brand",
                        "sku",
                        "gtin",
                        "price",
                        "currency",
                        "description",
                    ],
                )
            except Exception:
                logger.warning("ai_extraction_failed", exc_info=True)
            else:
                if ai_values:
                    ai_product = self._product_from_ai(ai_values, url)
                    if ai_product is not None:
                        product = merge_products(ai_product, product)
                        collector.add("product.ai", ai_values, ExtractionMethod.AI, confidence=0.35)
                        ai_used = True

        media = extract_media(document, metadata, include_media=include_media)

        if product is not None or any_product_value(product):
            page.page_type = ContentType.PRODUCT
        elif article is not None:
            page.page_type = ContentType.ARTICLE
        elif business is not None:
            page.page_type = ContentType.BUSINESS

        warnings: list[str] = []
        if not page.title:
            warnings.append("page title could not be determined")
        if not page.main_text and include_text:
            warnings.append("page main text could not be extracted")
        if (product is not None or page.page_type is ContentType.PRODUCT) and (
            product is None or product.price is None
        ):
            warnings.append("product price was not found in structured or visible data")

        structured_data: list[dict[str, Any]] = [
            *jsonld_items[:25],
            *microdata_items[:15],
            *rdfa_items[:10],
        ]
        method = self._method_name(
            jsonld_items=jsonld_items,
            microdata_items=microdata_items,
            rdfa_items=rdfa_items,
            plugin_used=plugin_used,
            ai_used=ai_used,
        )
        if browser_used:
            method = f"browser+{method}"
        has_content = bool(
            page.title or page.main_text or product is not None or article is not None or business is not None
        )
        status = ExtractionStatus.SUCCESS if has_content else ExtractionStatus.PARTIAL
        if not page.title:
            status = ExtractionStatus.PARTIAL
        return ExtractionPayload(
            requested_url=url,
            final_url=page.canonical_url or url,
            status=status,
            page=page,
            product=product,
            article=article,
            business=business,
            media=media,
            structured_data=structured_data,
            provenance=collector.fields,
            extraction_method=method,
            browser_used=browser_used,
            warnings=warnings,
        )

    @staticmethod
    def _method_name(
        *,
        jsonld_items: list[dict[str, Any]],
        microdata_items: list[dict[str, Any]],
        rdfa_items: list[dict[str, Any]],
        plugin_used: bool,
        ai_used: bool,
    ) -> str:
        if jsonld_items:
            return "json_ld"
        if microdata_items:
            return "microdata"
        if rdfa_items:
            return "rdfa"
        if plugin_used:
            return "plugin"
        if ai_used:
            return "ai"
        return "html"

    @staticmethod
    def _product_from_ai(values: dict[str, Any], url: str) -> ProductData | None:
        name = clean_text(str(values.get("name"))) if values.get("name") else None
        if not name:
            return None
        price = None
        amount = values.get("price")
        currency = values.get("currency")
        if amount is not None and currency:
            try:
                price = PriceData(
                    amount=float(amount),
                    currency=str(currency).upper(),
                    price_source=PriceSource.PAGE_VISIBLE,
                    verified=False,
                    verification_method="ai_fallback_unverified",
                )
            except (TypeError, ValueError):
                price = None
        images = [str(image) for image in values.get("images", []) if image]
        return ProductData(
            name=name,
            brand=clean_text(str(values.get("brand"))) if values.get("brand") else None,
            sku=clean_text(str(values.get("sku"))) if values.get("sku") else None,
            gtin=clean_text(str(values.get("gtin"))) if values.get("gtin") else None,
            description=clean_text(str(values.get("description"))) if values.get("description") else None,
            price=price,
            images=images,
            product_url=url,
        )

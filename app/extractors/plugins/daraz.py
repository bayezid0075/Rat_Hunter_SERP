"""Example website-specific extraction plugin for Daraz product pages."""

from __future__ import annotations

from bs4 import Tag

from app.extractors.base import Document, ProvenanceCollector, clean_text
from app.extractors.metadata import MetadataResult
from app.extractors.plugins.base import ExtractionPlugin, PluginExtraction
from app.processors.price import build_price
from app.schemas.common import ExtractionMethod
from app.schemas.product import Availability, PriceSource, ProductData


class DarazExtractionPlugin(ExtractionPlugin):
    name = "daraz"
    domains = ("daraz.com.bd", "daraz.com", "daraz.pk", "daraz.lk", "daraz.com.np")

    def extract(
        self,
        document: Document,
        metadata: MetadataResult,
        collector: ProvenanceCollector,
    ) -> PluginExtraction:
        soup = document.soup
        if soup is None:
            return PluginExtraction()
        name_tag = soup.select_one(".pdp-mod-product-badge-title") or soup.select_one("h1.pdp-title") or soup.find("h1")
        name = clean_text(name_tag.get_text(" ", strip=True)) if isinstance(name_tag, Tag) else None
        if not name:
            return PluginExtraction()

        price_tag = (
            soup.select_one(".pdp-price")
            or soup.select_one(".pdp-product-price")
            or soup.select_one("[class*='pdp-price']")
        )
        current_price = clean_text(price_tag.get_text(" ", strip=True)) if isinstance(price_tag, Tag) else None
        original_tag = soup.select_one(".pdp-price_type_deleted") or soup.select_one("del")
        original_price = clean_text(original_tag.get_text(" ", strip=True)) if isinstance(original_tag, Tag) else None
        currency = (
            metadata.open_graph.get("product:price:currency") or metadata.meta.get("product:price:currency") or "BDT"
        )
        price = (
            build_price(
                current_price,
                source=PriceSource.PAGE_VISIBLE,
                default_currency=currency,
                original_text=original_price,
                verification_method="daraz_plugin",
            )
            if current_price
            else None
        )
        brand_tag = soup.select_one(".pdp-product-brand") or soup.select_one("[class*='brand']")
        brand = clean_text(brand_tag.get_text(" ", strip=True)) if isinstance(brand_tag, Tag) else None
        stock_tag = soup.select_one(".product-stock") or soup.select_one("[class*='availability']")
        stock_text = (
            (clean_text(stock_tag.get_text(" ", strip=True)) or "") if isinstance(stock_tag, Tag) else ""
        ).casefold()
        availability = Availability.UNKNOWN
        if "in stock" in stock_text or "add to cart" in stock_text:
            availability = Availability.IN_STOCK
        elif "out of stock" in stock_text or "sold out" in stock_text:
            availability = Availability.OUT_OF_STOCK

        images: list[str] = []
        for tag in soup.select(".pdp-gallery img, .gallery-preview-panel img"):
            src = tag.get("src") if isinstance(tag, Tag) else None
            if src:
                absolute = document.absolute(str(src))
                if absolute and absolute not in images:
                    images.append(absolute)

        product = ProductData(
            name=name,
            brand=brand,
            price=price,
            availability=availability,
            images=images[:20],
            product_url=metadata.page.canonical_url or document.url,
        )
        collector.add("product.name", name, ExtractionMethod.PLUGIN, confidence=0.8)
        collector.add("product.brand", brand, ExtractionMethod.PLUGIN, confidence=0.7)
        if price:
            collector.add("product.price", price.amount, ExtractionMethod.PLUGIN, confidence=0.75)
            collector.add("product.currency", price.currency, ExtractionMethod.PLUGIN, confidence=0.75)
        return PluginExtraction(product=product)

"""Conservative HTML heuristics used only when structured data is missing."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.extractors.base import Document, ProvenanceCollector, clean_text
from app.extractors.metadata import MetadataResult, meta_lookup
from app.processors.price import build_price
from app.schemas.article import ArticleData
from app.schemas.business import BusinessData
from app.schemas.common import ContentType, ExtractionMethod
from app.schemas.media import MediaData, MediaType
from app.schemas.product import (
    Availability,
    PriceSource,
    ProductCondition,
    ProductData,
)
from app.utils.datetime import parse_datetime

_PRICE_SELECTORS = (
    "[itemprop='price']",
    "[data-price]",
    ".product-price",
    ".price",
    "[class*='price']",
)


def _attr_text(tag: Tag) -> str | None:
    for attr in ("content", "data-price", "value"):
        value = tag.get(attr)
        if value:
            return clean_text(str(value))
    return clean_text(tag.get_text(" ", strip=True))


def _first_price_text(soup: BeautifulSoup) -> str | None:
    for selector in _PRICE_SELECTORS:
        for tag in soup.select(selector):
            text = _attr_text(tag)
            if text and re.search(r"\d", text):
                return text
    return None


def _first_original_price_text(soup: BeautifulSoup) -> str | None:
    for selector in ("del", "s", ".old-price", ".compare-price", "[class*='old']"):
        for tag in soup.select(selector):
            text = clean_text(tag.get_text(" ", strip=True))
            if text and re.search(r"\d", text):
                return text
    return None


def _availability_from_text(text: str | None) -> Availability:
    lowered = (text or "").casefold()
    if any(term in lowered for term in ("out of stock", "sold out", "unavailable")):
        return Availability.OUT_OF_STOCK
    if any(term in lowered for term in ("pre-order", "preorder", "coming soon")):
        return Availability.PREORDER
    if any(term in lowered for term in ("back order", "backorder")):
        return Availability.BACKORDER
    if any(term in lowered for term in ("in stock", "available", "add to cart", "buy now")):
        return Availability.IN_STOCK
    return Availability.UNKNOWN


def _condition_from_text(text: str | None) -> ProductCondition:
    lowered = (text or "").casefold()
    if "refurbished" in lowered:
        return ProductCondition.REFURBISHED
    if "used" in lowered or "pre-owned" in lowered:
        return ProductCondition.USED
    if "new" in lowered:
        return ProductCondition.NEW
    return ProductCondition.UNKNOWN


def extract_product_heuristic(
    document: Document,
    metadata: MetadataResult,
    collector: ProvenanceCollector,
) -> ProductData | None:
    soup = document.soup
    page = metadata.page
    h1 = soup.find("h1")
    name = (
        clean_text(meta_lookup(metadata, "og:title", "product:name", "twitter:title"))
        or (clean_text(h1.get_text(" ", strip=True)) if isinstance(h1, Tag) else None)
        or page.title
    )
    if not name:
        return None

    price_default_currency = (
        meta_lookup(metadata, "product:price:currency", "og:price:currency")
        or meta_lookup(metadata, "product:sale_price:currency")
        or None
    )
    current_price_text = (
        meta_lookup(metadata, "product:price:amount", "og:price:amount")
        or meta_lookup(metadata, "product:sale_price:amount")
        or _first_price_text(soup)
    )
    original_price_text = meta_lookup(metadata, "product:original_price:amount")
    if not original_price_text:
        original_price_text = _first_original_price_text(soup)

    price = (
        build_price(
            current_price_text,
            source=PriceSource.PAGE_VISIBLE,
            default_currency=price_default_currency,
            original_text=original_price_text,
            verified=False,
            verification_method="visible_page_price",
        )
        if current_price_text
        else None
    )
    hint_text = f"{page.title or ''} {page.description or ''} {current_price_text or ''}"
    page_type = page.page_type
    if page_type is not ContentType.PRODUCT and price is None and "product" not in hint_text.casefold():
        return None

    brand = meta_lookup(metadata, "product:brand", "og:brand", "brand") or _itemprop(soup, "brand")
    sku = meta_lookup(metadata, "product:retailer_item_id", "sku") or _itemprop(soup, "sku")
    gtin = meta_lookup(metadata, "product:gtin", "gtin13", "gtin12", "gtin8") or _itemprop(soup, "gtin")
    availability_text = (
        _itemprop(soup, "availability")
        or meta_lookup(metadata, "product:availability", "og:availability")
        or _availability_hint(soup)
    )
    condition_text = _itemprop(soup, "itemCondition") or meta_lookup(metadata, "product:condition")
    images = page.images[:20]
    product = ProductData(
        name=name,
        description=page.description,
        brand=brand or None,
        sku=sku or None,
        gtin=gtin or None,
        price=price,
        availability=_availability_from_text(availability_text),
        condition=_condition_from_text(condition_text),
        images=images,
        product_url=page.canonical_url or document.url,
    )
    for field_name in (
        "name",
        "description",
        "brand",
        "sku",
        "gtin",
        "availability",
        "product_url",
    ):
        collector.add(
            f"product.{field_name}",
            getattr(product, field_name),
            ExtractionMethod.HTML,
            confidence=0.55,
        )
    if price:
        collector.add("product.price", price.amount, ExtractionMethod.HTML, confidence=0.55)
        collector.add("product.currency", price.currency, ExtractionMethod.HTML, confidence=0.55)
    for image in images:
        collector.add("product.images", image, ExtractionMethod.HTML, confidence=0.5)
    return product


def _itemprop(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find(attrs={"itemprop": name})  # type: ignore[call-overload]
    if not isinstance(tag, Tag):
        return None
    return _attr_text(tag)


def _availability_hint(soup: BeautifulSoup) -> str | None:
    for selector in (
        "[itemprop='availability']",
        ".stock",
        ".availability",
        "[class*='stock']",
        "[class*='availability']",
    ):
        for tag in soup.select(selector):
            text = clean_text(tag.get_text(" ", strip=True))
            if text:
                return text
    return None


def extract_article_heuristic(
    document: Document,
    metadata: MetadataResult,
    collector: ProvenanceCollector,
) -> ArticleData | None:
    soup = document.soup
    page = metadata.page
    article_tag = soup.find("article")
    if (
        page.page_type is not ContentType.ARTICLE
        and not isinstance(article_tag, Tag)
        and not page.author
        and not page.published_at
    ):
        return None
    h1 = article_tag.find("h1") if isinstance(article_tag, Tag) else None
    title = clean_text(h1.get_text(" ", strip=True)) if isinstance(h1, Tag) else page.title
    body = None
    if isinstance(article_tag, Tag):
        for tag in article_tag.find_all(("script", "style", "nav", "aside", "footer")):
            tag.decompose()
        body = clean_text(article_tag.get_text(" ", strip=True))
    if body is None and page.page_type is ContentType.ARTICLE:
        body = page.main_text
    article = ArticleData(
        title=title,
        author=page.author,
        publisher=page.publisher,
        published_at=page.published_at or parse_datetime(meta_lookup(metadata, "datepublished", "pubdate")),
        modified_at=page.modified_at,
        description=page.description,
        text=body,
        main_image=page.images[0] if page.images else None,
        word_count=len(body.split()) if body else None,
    )
    for field_name in ("title", "author", "publisher", "published_at", "description", "main_image"):
        collector.add(
            f"article.{field_name}",
            getattr(article, field_name),
            ExtractionMethod.HTML,
            confidence=0.5,
        )
    return article


def extract_business_heuristic(
    document: Document,
    metadata: MetadataResult,
    collector: ProvenanceCollector,
) -> BusinessData | None:
    soup = document.soup
    page = metadata.page
    og_type = (metadata.open_graph.get("og:type") or "").casefold()
    if page.page_type is not ContentType.BUSINESS and "business" not in og_type:
        return None
    name = page.title
    if not name:
        return None
    address_tag = soup.find("address")
    address = clean_text(address_tag.get_text(" ", strip=True)) if isinstance(address_tag, Tag) else None
    telephone = None
    for link in soup.find_all("a", href=True):
        href = str(link.get("href"))
        if href.startswith("tel:"):
            telephone = href.removeprefix("tel:").strip()
            break
    website = None
    canonical = page.canonical_url
    if canonical:
        website = canonical
    if not address and not telephone:
        return None
    business = BusinessData(
        name=name,
        address=address,
        telephone=telephone,
        website=website,
        category=og_type or None,
    )
    for field_name in ("name", "address", "telephone", "website", "category"):
        collector.add(
            f"business.{field_name}",
            getattr(business, field_name),
            ExtractionMethod.HTML,
            confidence=0.45,
        )
    return business


def extract_media(
    document: Document,
    metadata: MetadataResult,
    *,
    include_media: bool = True,
) -> list[MediaData]:
    if not include_media:
        return []
    media: list[MediaData] = []
    for url in metadata.page.images[:30]:
        media.append(MediaData(url=url, media_type=MediaType.IMAGE))
    video_sources = {
        "og:video": "twitter:player",
        "og:video:url": "twitter:player:stream",
        "og:video:secure_url": "twitter:player:stream",
        "twitter:player": "twitter:player",
    }
    for key, twitter_key in video_sources.items():
        value = metadata.open_graph.get(key) or metadata.twitter.get(twitter_key)
        if value:
            media.append(
                MediaData(
                    url=value,
                    video_url=value,
                    thumbnail=metadata.page.images[0] if metadata.page.images else None,
                    media_type=MediaType.VIDEO,
                )
            )
    for tag in document.soup.find_all("video"):
        src = tag.get("src")
        if not src:
            source = tag.find("source")
            if isinstance(source, Tag):
                src = source.get("src")
        if src:
            media.append(
                MediaData(
                    url=str(src),
                    video_url=str(src),
                    thumbnail=str(tag.get("poster")) if tag.get("poster") else None,
                    media_type=MediaType.VIDEO,
                )
            )
    seen: set[str] = set()
    unique: list[MediaData] = []
    for item in media:
        if item.url in seen:
            continue
        seen.add(item.url)
        unique.append(item)
    return unique[:40]


def _empty(value: Any, placeholders: set[Any]) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    try:
        return value in placeholders
    except TypeError:
        return False


def merge_products(primary: ProductData | None, fallback: ProductData | None) -> ProductData | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    placeholders = {Availability.UNKNOWN, ProductCondition.UNKNOWN}
    data = primary.model_dump()
    for key, value in fallback.model_dump().items():
        if _empty(data.get(key), placeholders) and not _empty(value, placeholders):
            data[key] = value
    return ProductData.model_validate(data)


def merge_articles(primary: ArticleData | None, fallback: ArticleData | None) -> ArticleData | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    data = primary.model_dump()
    for key, value in fallback.model_dump().items():
        if _empty(data.get(key), set()) and not _empty(value, set()):
            data[key] = value
    return ArticleData.model_validate(data)


def merge_businesses(primary: BusinessData | None, fallback: BusinessData | None) -> BusinessData | None:
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    data = primary.model_dump()
    for key, value in fallback.model_dump().items():
        if _empty(data.get(key), set()) and not _empty(value, set()):
            data[key] = value
    return BusinessData.model_validate(data)


def any_product_value(product: ProductData | None) -> bool:
    if product is None:
        return False
    return bool(product.name or product.price or product.sku or product.gtin)

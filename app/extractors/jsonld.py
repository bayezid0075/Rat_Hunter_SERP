"""JSON-LD / schema.org structured data extraction."""

from __future__ import annotations

import json
import logging
from typing import Any

from bs4 import BeautifulSoup

from app.extractors.base import ProvenanceCollector, clean_text
from app.schemas.article import ArticleData
from app.schemas.business import BusinessData
from app.schemas.common import ExtractedField, ExtractionMethod
from app.schemas.product import (
    Availability,
    PriceData,
    PriceSource,
    ProductCondition,
    ProductData,
    SellerData,
)
from app.utils.datetime import parse_datetime

logger = logging.getLogger(__name__)

_PRODUCT_TYPES = {
    "product",
    "productmodel",
    "individualproduct",
    "productgroup",
    "vehicle",
    "book",
    "softwareapplication",
    "mobileapplication",
}
_ARTICLE_TYPES = {
    "article",
    "newsarticle",
    "blogposting",
    "techarticle",
    "report",
    "scholarlyarticle",
    "recipe",
}
_BUSINESS_TYPES = {
    "localbusiness",
    "organization",
    "store",
    "restaurant",
    "corporation",
    "professionalservice",
    "medicalbusiness",
    "onlinebusiness",
}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _type_names(item: dict[str, Any]) -> set[str]:
    raw = item.get("@type") or item.get("type") or ""
    if isinstance(raw, str):
        raw = [raw]
    return {str(value).split("/")[-1].split(":")[-1].casefold() for value in _as_list(raw) if value}


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, "", []):
            return value
    return None


def _name_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_text(str(_first(value, "name", "legalName", "headline") or ""))
    if isinstance(value, list):
        names = [name for name in (_name_of(item) for item in value) if name]
        return ", ".join(names) or None
    return clean_text(str(value)) if value else None


def _text_of(value: Any) -> str | None:
    if isinstance(value, dict):
        return clean_text(str(_first(value, "description", "text", "name") or "")) or None
    if isinstance(value, list):
        texts = [text for text in (_text_of(item) for item in value) if text]
        return " ".join(texts) or None
    return clean_text(str(value)) if value else None


def _extract_price(offers: Any) -> PriceData | None:
    for offer in _as_list(offers):
        if not isinstance(offer, dict):
            continue
        amount = _first(offer, "price", "lowPrice", "highPrice")
        currency = _first(offer, "priceCurrency", "currency")
        if amount is None or currency is None:
            continue
        try:
            parsed_amount = float(str(amount).replace(",", "").strip())
        except ValueError:
            continue
        if parsed_amount < 0:
            continue
        return PriceData(
            amount=parsed_amount,
            currency=str(currency).upper(),
            price_source=PriceSource.STRUCTURED_DATA,
            verified=False,
            verification_method="json_ld_offer",
        )
    return None


def _map_availability(value: Any) -> Availability:
    text = str(value or "").casefold()
    if "instock" in text or "in stock" in text:
        return Availability.IN_STOCK
    if "outofstock" in text or "out of stock" in text or "soldout" in text:
        return Availability.OUT_OF_STOCK
    if "preorder" in text:
        return Availability.PREORDER
    if "backorder" in text:
        return Availability.BACKORDER
    if "discontinued" in text:
        return Availability.DISCONTINUED
    return Availability.UNKNOWN


def _map_condition(value: Any) -> ProductCondition:
    text = str(value or "").casefold()
    if "refurbished" in text:
        return ProductCondition.REFURBISHED
    if "used" in text:
        return ProductCondition.USED
    if "new" in text:
        return ProductCondition.NEW
    return ProductCondition.UNKNOWN


def _images(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            url = _first(item, "url", "contentUrl", "thumbnailUrl")
            if isinstance(url, str):
                result.append(url)
        elif isinstance(item, list):
            result.extend(_images(item))
    return list(dict.fromkeys(result))


def _seller(offers: Any) -> SellerData | None:
    for offer in _as_list(offers):
        if not isinstance(offer, dict):
            continue
        seller = offer.get("seller")
        if isinstance(seller, dict):
            return SellerData(
                name=_name_of(seller),
                url=str(_first(seller, "url") or "") or None,
            )
    return None


def iter_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """Parse every JSON-LD block, flattening ``@graph`` containers."""

    items: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string if script.string is not None else script.get_text()
        if not raw or not raw.strip():
            continue
        payload = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("json_ld_parse_failed")
            continue
        for item in _as_list(parsed):
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                items.extend(entry for entry in graph if isinstance(entry, dict))
            else:
                items.append(item)
    return items


def classify_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    classified: dict[str, list[dict[str, Any]]] = {
        "products": [],
        "articles": [],
        "businesses": [],
        "other": [],
    }
    for item in items:
        types = _type_names(item)
        if types & _PRODUCT_TYPES:
            classified["products"].append(item)
        elif types & _ARTICLE_TYPES:
            classified["articles"].append(item)
        elif types & _BUSINESS_TYPES:
            classified["businesses"].append(item)
        else:
            classified["other"].append(item)
    return classified


def product_from_item(
    item: dict[str, Any],
    *,
    method: ExtractionMethod = ExtractionMethod.JSON_LD,
    source_url: str | None = None,
    collector: ProvenanceCollector | None = None,
) -> ProductData | None:
    name = _first(item, "name", "headline")
    if not name and not item.get("offers"):
        return None
    offers = item.get("offers")
    offer_items = [offer for offer in _as_list(offers) if isinstance(offer, dict)]
    price = _extract_price(offers)
    images = _images(_first(item, "image", "thumbnailUrl", "logo"))
    product = ProductData(
        name=clean_text(str(name)) if name else None,
        description=_text_of(item.get("description")),
        brand=_name_of(item.get("brand")),
        sku=_text_of(_first(item, "sku", "productID", "identifier")),
        gtin=_text_of(_first(item, "gtin", "gtin13", "gtin12", "gtin8")),
        mpn=_text_of(item.get("mpn")),
        price=price,
        availability=(
            _map_availability(_first(offer_items[0], "availability")) if offer_items else Availability.UNKNOWN
        ),
        condition=_map_condition(item.get("itemCondition")),
        images=images,
        product_url=str(_first(item, "url", "mainEntityOfPage") or "") or None,
        seller=_seller(offers),
        rating=_rating(item.get("aggregateRating")),
        review_count=_review_count(item.get("aggregateRating")),
        category=_name_of(item.get("category")),
    )
    if collector:
        _record_product_provenance(collector, product, method)
    return product


def _rating(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("ratingValue")
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _review_count(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = _first(value, "reviewCount", "ratingCount")
    try:
        return int(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _record_product_provenance(collector: ProvenanceCollector, product: ProductData, method: ExtractionMethod) -> None:
    for field_name in (
        "name",
        "description",
        "brand",
        "sku",
        "gtin",
        "mpn",
        "availability",
        "product_url",
        "category",
    ):
        collector.add(f"product.{field_name}", getattr(product, field_name), method, confidence=0.9)
    if product.price:
        collector.add("product.price", product.price.amount, method, confidence=0.9)
        collector.add("product.currency", product.price.currency, method, confidence=0.9)
    for image in product.images:
        collector.add("product.images", image, method, confidence=0.8)


def article_from_item(
    item: dict[str, Any],
    *,
    method: ExtractionMethod = ExtractionMethod.JSON_LD,
    collector: ProvenanceCollector | None = None,
) -> ArticleData | None:
    title = _first(item, "headline", "name")
    body = _first(item, "articleBody", "text")
    if not title and not body:
        return None
    images = _images(_first(item, "image", "thumbnailUrl"))
    article = ArticleData(
        title=clean_text(str(title)) if title else None,
        author=_name_of(item.get("author")),
        publisher=_name_of(item.get("publisher")),
        published_at=parse_datetime(_first(item, "datePublished", "dateCreated")),
        modified_at=parse_datetime(_first(item, "dateModified")),
        description=_text_of(item.get("description")),
        text=clean_text(str(body)) if body else None,
        main_image=images[0] if images else None,
        word_count=_word_count(_first(item, "wordCount"), body),
        categories=[str(value) for value in _as_list(item.get("articleSection")) if value],
    )
    if collector:
        for field_name in (
            "title",
            "author",
            "publisher",
            "published_at",
            "modified_at",
            "description",
            "main_image",
        ):
            collector.add(f"article.{field_name}", getattr(article, field_name), method, confidence=0.85)
    return article


def _word_count(raw: Any, body: Any) -> int | None:
    try:
        if raw is not None:
            return int(str(raw))
    except (TypeError, ValueError):
        pass
    if isinstance(body, str):
        return len(body.split())
    return None


def _format_address(value: Any) -> str | None:
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        formatted = [_format_address(item) for item in value]
        return "; ".join(item for item in formatted if item) or None
    if not isinstance(value, dict):
        return None
    parts = [
        _first(value, "streetAddress"),
        _first(value, "addressLocality"),
        _first(value, "addressRegion"),
        _first(value, "postalCode"),
        _name_of(value.get("addressCountry")),
    ]
    cleaned = [clean_text(str(part)) for part in parts if part]
    return ", ".join(part for part in cleaned if part) or None


def business_from_item(
    item: dict[str, Any],
    *,
    method: ExtractionMethod = ExtractionMethod.JSON_LD,
    collector: ProvenanceCollector | None = None,
) -> BusinessData | None:
    name = _first(item, "name", "legalName")
    if not name:
        return None
    geo = item.get("geo")
    geo_dict: dict[str, Any] = {}
    if isinstance(geo, dict):
        geo_dict = {key: geo.get(key) for key in ("latitude", "longitude", "elevation") if geo.get(key) is not None}
    address = _format_address(item.get("address"))
    hours = _as_list(_first(item, "openingHours", "openingHoursSpecification"))
    opening_hours = []
    for value in hours:
        if isinstance(value, str):
            opening_hours.append(value)
        elif isinstance(value, dict):
            days = _first(value, "dayOfWeek", "name")
            if isinstance(days, list):
                days = ", ".join(str(day) for day in days)
            opens = _first(value, "opens")
            closes = _first(value, "closes")
            if days and opens and closes:
                opening_hours.append(f"{days} {opens}-{closes}")
    types = _type_names(item)
    business = BusinessData(
        name=clean_text(str(name)),
        address=address,
        telephone=_text_of(_first(item, "telephone", "phone")),
        website=str(_first(item, "url", "sameAs") or "") or None,
        category=next(iter(types), None),
        geo=geo_dict,
        opening_hours=opening_hours,
        rating=_rating(item.get("aggregateRating")),
    )
    if collector:
        for field_name in ("name", "address", "telephone", "website", "category"):
            collector.add(f"business.{field_name}", getattr(business, field_name), method, confidence=0.85)
    return business


def extract_structured(
    items: list[dict[str, Any]],
    *,
    source_url: str,
    collector: ProvenanceCollector,
    method: ExtractionMethod = ExtractionMethod.JSON_LD,
) -> tuple[ProductData | None, ArticleData | None, BusinessData | None, list[ExtractedField]]:
    classified = classify_items(items)
    start = len(collector.fields)
    product = next(
        (
            product_candidate
            for item in classified["products"]
            if (
                product_candidate := product_from_item(
                    item,
                    method=method,
                    source_url=source_url,
                    collector=collector,
                )
            )
            is not None
        ),
        None,
    )
    article = next(
        (
            article_candidate
            for item in classified["articles"]
            if (article_candidate := article_from_item(item, method=method, collector=collector)) is not None
        ),
        None,
    )
    business = next(
        (
            business_candidate
            for item in classified["businesses"]
            if (business_candidate := business_from_item(item, method=method, collector=collector)) is not None
        ),
        None,
    )
    return product, article, business, collector.fields[start:]

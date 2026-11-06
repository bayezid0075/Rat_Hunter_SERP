"""Duplicate detection across search sources and extraction results."""

from __future__ import annotations

import re

from app.processors.normalize import domain_from_url, normalize_url
from app.schemas.search import SearchResult


def _product_identifier(result: SearchResult) -> str | None:
    product = result.extraction.product if result.extraction else None
    if product is None:
        return None
    for field_name in ("gtin", "sku", "mpn"):
        value = getattr(product, field_name)
        if value:
            cleaned = re.sub(r"[^a-z0-9]", "", str(value).casefold())
            if cleaned:
                return f"{field_name}:{cleaned}"
    return None


def _title_key(result: SearchResult) -> str | None:
    if not result.title:
        return None
    product = result.extraction.product if result.extraction else None
    if product is not None and (product.gtin or product.sku or product.mpn):
        return None
    domain = result.domain or domain_from_url(result.url)
    normalized_title = " ".join(result.title.casefold().split())
    if not domain or not normalized_title:
        return None
    return f"title:{domain}:{normalized_title}"


def result_dedup_key(result: SearchResult) -> str:
    identifier = _product_identifier(result)
    if identifier:
        return identifier
    canonical = result.canonical_url or result.url
    normalized = normalize_url(canonical)
    if normalized:
        return f"url:{normalized}"
    title_key = _title_key(result)
    return title_key or f"raw:{result.source}:{result.url}"


def _quality(result: SearchResult) -> tuple[int, float, int]:
    has_extraction = 1 if result.extraction is not None else 0
    return has_extraction, float(result.score or 0.0), -result.position


def deduplicate_results(results: list[SearchResult]) -> list[SearchResult]:
    best: dict[str, SearchResult] = {}
    order: list[str] = []
    for result in results:
        key = result_dedup_key(result)
        existing = best.get(key)
        if existing is None:
            best[key] = result
            order.append(key)
        elif _quality(result) > _quality(existing):
            best[key] = result
    return [best[key] for key in order]

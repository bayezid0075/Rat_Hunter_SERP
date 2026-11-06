"""Independent, configurable search ranking."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from app.processors.normalize import domain_from_url, query_tokens
from app.schemas.common import ContentType
from app.schemas.search import SearchResult, SearchType

DEFAULT_WEIGHTS: dict[str, float] = {
    "position": 0.30,
    "title_relevance": 0.22,
    "query_similarity": 0.16,
    "domain_relevance": 0.10,
    "content_type": 0.08,
    "source_reliability": 0.08,
    "freshness": 0.06,
}

SOURCE_RELIABILITY: dict[str, float] = {
    "google": 1.0,
    "custom_index": 0.8,
    "direct_daraz": 0.7,
    "direct_pickaboo": 0.7,
    "direct_startech": 0.7,
    "direct_gadgetandgear": 0.7,
}


class RankingEngine:
    def __init__(self, weights: dict[str, float] | None = None) -> None:
        merged = dict(DEFAULT_WEIGHTS)
        if weights:
            for key, value in weights.items():
                if key in merged:
                    merged[key] = float(value)
        self.weights = merged
        self._weight_total = sum(merged.values()) or 1.0

    def rank(
        self,
        results: list[SearchResult],
        query: str,
        *,
        search_type: SearchType = SearchType.WEB,
    ) -> list[SearchResult]:
        if not results:
            return results
        for result in results:
            factors = self._factors(result, query, search_type)
            score = sum(factors[key] * self.weights[key] for key in self.weights)
            result.score = round(min(1.0, max(0.0, score / self._weight_total)), 6)
            result.rank_factors = {key: round(value, 6) for key, value in factors.items()}
            result.rank_factors["source_position"] = float(result.position)
        ranked = sorted(
            results,
            key=lambda item: (-(item.score or 0.0), item.position, item.title.casefold()),
        )
        return [item.model_copy(update={"position": index + 1}) for index, item in enumerate(ranked)]

    def _factors(self, result: SearchResult, query: str, search_type: SearchType) -> dict[str, float]:
        tokens = query_tokens(query)
        title_tokens = query_tokens(result.title or "")
        snippet_tokens = query_tokens(result.snippet or "")
        overlap = len(tokens & (title_tokens | snippet_tokens))
        title_relevance = overlap / len(tokens) if tokens else 0.0
        if tokens and result.title:
            similarity = SequenceMatcher(None, query.casefold(), result.title.casefold()).ratio()
            similarity = max(similarity, len(tokens & title_tokens) / len(tokens))
        else:
            similarity = 0.0
        domain = result.domain or domain_from_url(result.url)
        domain_parts = set(re.findall(r"[a-z0-9]+", domain.casefold()))
        domain_relevance = (
            1.0 if tokens and tokens & domain_parts else (0.35 if domain.endswith((".gov", ".edu", ".org")) else 0.15)
        )
        position_factor = 1.0 / (1.0 + 0.18 * max(0, result.position - 1))
        content_factor = self._content_factor(result, search_type)
        reliability = SOURCE_RELIABILITY.get(result.source, 0.5)
        freshness = self._freshness(result)
        return {
            "position": position_factor,
            "title_relevance": min(1.0, title_relevance),
            "query_similarity": min(1.0, similarity),
            "domain_relevance": domain_relevance,
            "content_type": content_factor,
            "source_reliability": reliability,
            "freshness": freshness,
        }

    @staticmethod
    def _content_factor(result: SearchResult, search_type: SearchType) -> float:
        if search_type is SearchType.PRODUCTS:
            return 1.0 if result.content_type is ContentType.PRODUCT else 0.35
        if search_type is SearchType.NEWS:
            return 1.0 if result.content_type is ContentType.ARTICLE else 0.4
        return 0.7 if result.content_type is ContentType.WEBPAGE else 0.55

    @staticmethod
    def _freshness(result: SearchResult) -> float:
        published: Any = result.published_at
        if published is None:
            return 0.5
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - published).total_seconds() / 86_400)
        return math.exp(-age_days / 30.0)

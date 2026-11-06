"""Search request and response schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.common import (
    ContentType,
    ExtractionStatus,
    SearchMetadata,
    SearchMode,
    SearchType,
)
from app.schemas.extraction import ExtractionPayload
from app.schemas.product import PriceData

__all__ = [
    "ProductSearchRequest",
    "SearchMode",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "SearchType",
]

_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=512)
    country: str = Field(default="US", min_length=2, max_length=2)
    language: str = Field(default="en", min_length=2, max_length=8)
    search_type: SearchType = SearchType.WEB
    mode: SearchMode = SearchMode.QUICK
    limit: int = Field(default=10, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=10)
    safe_search: bool = True
    include_domains: list[str] = Field(default_factory=list, max_length=20)
    exclude_domains: list[str] = Field(default_factory=list, max_length=20)
    sources: list[str] | None = Field(default=None, max_length=20)
    use_cache: bool = True
    force_refresh: bool = False
    async_job: bool = Field(default=False, alias="async")
    extract_limit: int | None = Field(default=None, ge=0, le=20)
    timeout_ms: int | None = Field(default=None, ge=500, le=60_000)

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("query cannot be blank")
        return cleaned

    @field_validator("country")
    @classmethod
    def _validate_country(cls, value: str) -> str:
        if not _COUNTRY_RE.match(value):
            raise ValueError("country must be an ISO 3166-1 alpha-2 code")
        return value.upper()

    @field_validator("language")
    @classmethod
    def _validate_language(cls, value: str) -> str:
        if not _LANGUAGE_RE.match(value):
            raise ValueError("language must be a valid BCP-47-like code")
        return value.lower()

    @field_validator("include_domains", "exclude_domains")
    @classmethod
    def _validate_domains(cls, value: list[str]) -> list[str]:
        result: list[str] = []
        for item in value:
            domain = item.strip().lower().removeprefix("www.")
            if not _DOMAIN_RE.match(domain):
                raise ValueError(f"invalid domain: {item}")
            result.append(domain)
        return result

    @model_validator(mode="after")
    def _check_domain_filters(self) -> SearchRequest:
        overlap = set(self.include_domains) & set(self.exclude_domains)
        if overlap:
            raise ValueError(f"domains cannot be both included and excluded: {sorted(overlap)}")
        return self


class ProductSearchRequest(SearchRequest):
    search_type: Literal[SearchType.PRODUCTS] = SearchType.PRODUCTS
    min_price: float | None = Field(default=None, ge=0)
    max_price: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @field_validator("currency")
    @classmethod
    def _uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper() if value else value


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1)
    title: str
    url: str
    canonical_url: str | None = None
    domain: str | None = None
    snippet: str | None = None
    content_type: ContentType = ContentType.WEBPAGE
    source: str
    published_at: datetime | None = None
    thumbnail: str | None = None
    extraction_status: ExtractionStatus = ExtractionStatus.NOT_REQUESTED
    snippet_price: PriceData | None = None
    extraction: ExtractionPayload | None = None
    score: float | None = None
    rank_factors: dict[str, float] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    metadata: SearchMetadata
    results: list[SearchResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

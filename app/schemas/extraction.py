"""Extraction request and response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.article import ArticleData
from app.schemas.business import BusinessData
from app.schemas.common import (
    ContentType,
    ErrorDetail,
    ExtractedField,
    ExtractionStatus,
    utcnow,
)
from app.schemas.media import MediaData
from app.schemas.product import ProductData


def validate_url_string(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 2048:
        raise ValueError("URL must be between 1 and 2048 characters")
    parts = urlsplit(value)
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("only http and https URLs are supported")
    if not parts.hostname:
        raise ValueError("URL must include a hostname")
    if parts.username or parts.password:
        raise ValueError("credentials in URLs are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("URL contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("URL contains an invalid port")
    return value


class ExtractionOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_text: bool = True
    include_media: bool = True
    allow_browser: bool = True
    use_cache: bool = True
    force_refresh: bool = False
    timeout_ms: int | None = Field(default=None, ge=500, le=60_000)


class ExtractionRequest(ExtractionOptions):
    url: str
    async_job: bool = Field(default=False, alias="async")

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        return validate_url_string(value)


class BatchExtractionRequest(ExtractionOptions):
    urls: list[str] = Field(min_length=1, max_length=20)
    async_job: bool = Field(default=False, alias="async")

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    @field_validator("urls")
    @classmethod
    def _validate_urls(cls, value: list[str]) -> list[str]:
        return [validate_url_string(url) for url in value]


class HeadingData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=6)
    text: str


class PageData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    canonical_url: str | None = None
    domain: str | None = None
    language: str | None = None
    page_type: ContentType = ContentType.WEBPAGE
    main_text: str | None = None
    headings: list[HeadingData] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    open_graph: dict[str, str] = Field(default_factory=dict)
    twitter: dict[str, str] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    favicon: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    author: str | None = None
    publisher: str | None = None


class ExtractionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_url: str
    final_url: str
    status: ExtractionStatus = ExtractionStatus.SUCCESS
    page: PageData
    product: ProductData | None = None
    article: ArticleData | None = None
    business: BusinessData | None = None
    media: list[MediaData] = Field(default_factory=list)
    structured_data: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[ExtractedField] = Field(default_factory=list)
    extraction_method: str = "html"
    browser_used: bool = False
    extracted_at: datetime = Field(default_factory=utcnow)
    warnings: list[str] = Field(default_factory=list)


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    requested_url: str
    data: ExtractionPayload
    processing_time_ms: float = 0.0
    cached: bool = False
    stale: bool = False
    request_id: str | None = None


class BatchExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = True
    results: list[ExtractionPayload] = Field(default_factory=list)
    errors: list[ErrorDetail] = Field(default_factory=list)
    processing_time_ms: float = 0.0
    request_id: str | None = None

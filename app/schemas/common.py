"""Shared schemas and enumerations."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class SearchType(StrEnum):
    WEB = "web"
    PRODUCTS = "products"
    NEWS = "news"
    IMAGES = "images"


class SearchMode(StrEnum):
    QUICK = "quick"
    DEEP = "deep"


class ContentType(StrEnum):
    WEBPAGE = "webpage"
    PRODUCT = "product"
    ARTICLE = "article"
    BUSINESS = "business"
    MEDIA = "media"
    OTHER = "other"


class ExtractionStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class FieldValidationStatus(StrEnum):
    VALID = "valid"
    UNVERIFIED = "unverified"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class ExtractionMethod(StrEnum):
    JSON_LD = "json_ld"
    MICRODATA = "microdata"
    RDFA = "rdfa"
    OPEN_GRAPH = "open_graph"
    HTML_META = "html_meta"
    HTML = "html"
    PLUGIN = "plugin"
    BROWSER = "browser"
    AI = "ai"
    SNIPPET = "snippet"


class SourceHealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    DISABLED = "disabled"
    UNKNOWN = "unknown"


class ExtractedField(BaseModel):
    """A value plus its extraction provenance."""

    model_config = ConfigDict(extra="forbid")

    field: str
    value: Any = None
    method: ExtractionMethod | str = ExtractionMethod.HTML
    source_url: str | None = None
    extracted_at: datetime = Field(default_factory=utcnow)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    validation_status: FieldValidationStatus = FieldValidationStatus.UNVERIFIED


class SourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: SourceHealthStatus
    latency_ms: float | None = None
    result_count: int = 0
    error_code: str | None = None
    error: str | None = None
    retried: bool = False


class SearchMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    country: str
    language: str
    search_type: SearchType
    mode: SearchMode = SearchMode.QUICK
    total_results: int = 0
    processing_time_ms: float = 0.0
    cached: bool = False
    stale: bool = False
    page: int = 1
    limit: int = 10
    sources_used: list[str] = Field(default_factory=list)
    source_statuses: list[SourceStatus] = Field(default_factory=list)
    request_id: str | None = None


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool = False
    error: ErrorDetail
    request_id: str | None = None


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SourceHealthStatus
    detail: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    version: str
    environment: str
    uptime_seconds: float
    components: dict[str, ComponentHealth] = Field(default_factory=dict)


class ReadyResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    checks: dict[str, bool] = Field(default_factory=dict)

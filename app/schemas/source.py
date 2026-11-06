"""Search source discovery and health schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import SourceHealthStatus, utcnow


class SourceInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    adapter_type: str
    enabled: bool
    capabilities: list[str] = Field(default_factory=list)
    concurrency_limit: int
    requires_browser: bool = False
    description: str | None = None


class SourceHealthItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: SourceHealthStatus
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime = Field(default_factory=utcnow)


class SourceHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[SourceHealthItem] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utcnow)

"""Account and usage schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    rate_limit_per_minute: int | None = None
    daily_quota: int | None = None


class AccountInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    email: str | None = None
    plan: str
    is_active: bool
    application: dict[str, Any] | None = None
    api_key: ApiKeyInfo


class EndpointUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    requests: int
    errors: int
    average_latency_ms: float


class DailyUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    requests: int
    errors: int


class UsageStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_requests: int = 0
    error_requests: int = 0
    cached_requests: int = 0
    total_units: int = 0
    by_endpoint: list[EndpointUsage] = Field(default_factory=list)
    daily: list[DailyUsage] = Field(default_factory=list)
    quota: int | None = None
    quota_remaining: int | None = None
    rate_limit_per_minute: int | None = None


class UsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: UUID
    period_start: datetime
    period_end: datetime
    stats: UsageStats

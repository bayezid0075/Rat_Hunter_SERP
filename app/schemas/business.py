"""Local business extraction schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BusinessData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    telephone: str | None = None
    website: str | None = None
    category: str | None = None
    geo: dict[str, Any] = Field(default_factory=dict)
    opening_hours: list[str] = Field(default_factory=list)
    rating: float | None = None

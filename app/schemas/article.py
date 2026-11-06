"""Article extraction schema."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArticleData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    description: str | None = None
    text: str | None = None
    main_image: str | None = None
    word_count: int | None = Field(default=None, ge=0)
    categories: list[str] = Field(default_factory=list)

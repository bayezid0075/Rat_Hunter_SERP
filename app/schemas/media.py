"""Media extraction schema."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MediaType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    OTHER = "other"


class MediaData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    alt: str | None = None
    video_url: str | None = None
    thumbnail: str | None = None
    media_type: MediaType = MediaType.IMAGE
    width: int | None = None
    height: int | None = None

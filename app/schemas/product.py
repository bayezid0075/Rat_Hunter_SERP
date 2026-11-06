"""Product and price schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import utcnow


class Availability(StrEnum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    BACKORDER = "backorder"
    DISCONTINUED = "discontinued"
    UNKNOWN = "unknown"


class ProductCondition(StrEnum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"
    UNKNOWN = "unknown"


class PriceSource(StrEnum):
    SEARCH_SNIPPET = "search_snippet"
    STRUCTURED_DATA = "structured_data"
    PAGE_VISIBLE = "page_visible"
    VERIFIED_OFFER = "verified_offer"


class SellerData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    url: str | None = None
    rating: float | None = None
    seller_type: str | None = None


class PriceData(BaseModel):
    """A monetary value with explicit provenance.

    Prices from different sources are never silently merged.  ``verified`` is
    only true when the extraction pipeline confirmed the offer in structured
    data or the visible page body.
    """

    model_config = ConfigDict(extra="forbid")

    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    original_amount: float | None = Field(default=None, ge=0)
    discount_amount: float | None = Field(default=None, ge=0)
    discount_percent: float | None = Field(default=None, ge=0, le=100)
    price_source: PriceSource = PriceSource.PAGE_VISIBLE
    verified: bool = False
    verification_method: str | None = None
    extracted_at: datetime = Field(default_factory=utcnow)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, value: str) -> str:
        return value.strip().upper()


class ProductData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    brand: str | None = None
    sku: str | None = None
    gtin: str | None = None
    mpn: str | None = None
    price: PriceData | None = None
    availability: Availability = Availability.UNKNOWN
    condition: ProductCondition = ProductCondition.UNKNOWN
    variants: list[dict[str, Any]] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    product_url: str | None = None
    seller: SellerData | None = None
    rating: float | None = Field(default=None, ge=0)
    review_count: int | None = Field(default=None, ge=0)
    category: str | None = None

"""Documents available to the internal index adapter.

This is deliberately a small, operator-managed index.  It is *not* an internet
search engine; it lets the platform search documents that an operator has
explicitly indexed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin, UUIDMixin


class IndexedDocument(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "indexed_documents"

    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True, nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    domain: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    page_type: Mapped[str] = mapped_column(String(32), default="webpage", nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

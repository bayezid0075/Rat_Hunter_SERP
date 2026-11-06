"""Search the platform's own operator-managed document index.

This adapter is deliberately *not* presented as a full internet search engine.
It only searches rows that an operator has inserted into ``indexed_documents``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.config import Settings
from app.core.exceptions import DatabaseUnavailableError
from app.processors.normalize import query_tokens
from app.schemas.common import ContentType, SearchType, SourceHealthStatus
from app.schemas.search import SearchResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _content_type(value: str) -> ContentType:
    try:
        return ContentType(value)
    except ValueError:
        return ContentType.WEBPAGE


class CustomIndexAdapter(SearchAdapter):
    name = "custom_index"
    adapter_type = "internal_index"
    capabilities = (SearchType.WEB, SearchType.PRODUCTS, SearchType.NEWS)
    description = "Searches the operator-managed internal document index"

    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.concurrency_limit = settings.per_adapter_concurrency

    async def search(self, options: SearchOptions) -> list[SearchResult]:
        from app.models.indexed_document import IndexedDocument as Document

        tokens = sorted(query_tokens(options.query))[:8] or [options.query.strip()]
        conditions = []
        for token in tokens:
            pattern = f"%{_escape_like(token)}%"
            conditions.append(
                or_(
                    Document.title.ilike(pattern, escape="\\"),
                    Document.content.ilike(pattern, escape="\\"),
                )
            )
        stmt = select(Document).where(*conditions).order_by(Document.updated_at.desc()).limit(options.limit * 3)
        if options.search_type is SearchType.PRODUCTS:
            stmt = stmt.where(Document.page_type == ContentType.PRODUCT.value)
        try:
            async with self.session_factory() as session:
                rows = (await session.execute(stmt)).scalars().all()
        except SQLAlchemyError as exc:
            raise DatabaseUnavailableError("internal index query failed") from exc
        results: list[SearchResult] = []
        for row in rows:
            snippet = " ".join(row.content.split())[:300] or None
            metadata = row.doc_metadata or {}
            thumbnail = None
            images = metadata.get("images")
            if isinstance(images, list) and images:
                thumbnail = str(images[0])
            results.append(
                SearchResult(
                    position=len(results) + 1,
                    title=row.title,
                    url=row.url,
                    canonical_url=row.canonical_url or row.url,
                    domain=row.domain,
                    snippet=snippet,
                    content_type=_content_type(row.page_type),
                    source=self.name,
                    published_at=row.published_at,
                    thumbnail=thumbnail,
                )
            )
            if len(results) >= options.limit:
                break
        return results

    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        try:
            async with self.session_factory() as session:
                await session.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.UNAVAILABLE,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc.__class__.__name__),
            )
        return AdapterHealth(
            name=self.name,
            status=SourceHealthStatus.OK,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

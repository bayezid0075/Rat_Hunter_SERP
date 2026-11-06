"""Two-stage extraction service with cache, browser fallback and batch support."""

from __future__ import annotations

import asyncio
import time

from app.config import Settings
from app.core.cache import CacheManager, extraction_cache_key
from app.core.concurrency import ConcurrencyManager
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.security import validate_url_syntax
from app.extractors.browser import BrowserPool
from app.extractors.http import HttpFetcher
from app.extractors.pipeline import ExtractionPipeline
from app.monitoring.metrics import CACHE_HITS, CACHE_MISSES, EXTRACTION_LATENCY
from app.processors.normalize import normalize_url
from app.schemas.common import ErrorDetail, ExtractionStatus
from app.schemas.extraction import (
    BatchExtractionResponse,
    ExtractionPayload,
    ExtractionResponse,
)

logger = get_logger(__name__)


class ExtractionService:
    def __init__(
        self,
        *,
        settings: Settings,
        cache: CacheManager,
        concurrency: ConcurrencyManager,
        http_fetcher: HttpFetcher,
        browser_pool: BrowserPool,
        pipeline: ExtractionPipeline,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.concurrency = concurrency
        self.http_fetcher = http_fetcher
        self.browser_pool = browser_pool
        self.pipeline = pipeline

    async def extract_url(
        self,
        url: str,
        *,
        include_text: bool = True,
        include_media: bool = True,
        allow_browser: bool = True,
        use_cache: bool = True,
        force_refresh: bool = False,
        timeout: float | None = None,
        request_id: str | None = None,
    ) -> ExtractionResponse:
        started = time.perf_counter()
        validate_url_syntax(
            url,
            allowed_ports=self.settings.http_allowed_ports,
            allow_private=self.settings.ssrf_allow_private,
        )
        timeout = max(0.5, timeout or self.settings.extraction_timeout)
        cache_key = extraction_cache_key(normalize_url(url), include_text=include_text, include_media=include_media)

        async def factory() -> dict[str, object]:
            payload = await self._extract_once(
                url,
                include_text=include_text,
                include_media=include_media,
                allow_browser=allow_browser,
                timeout=timeout,
            )
            return payload.model_dump(mode="json")

        entry = await self.cache.get_or_set(
            cache_key,
            factory,
            fresh_ttl=self.settings.extraction_cache_ttl,
            stale_ttl=self.settings.extraction_stale_ttl,
            use_cache=use_cache,
            force_refresh=force_refresh,
        )
        payload = ExtractionPayload.model_validate(entry.value)
        elapsed = (time.perf_counter() - started) * 1000
        if entry.cached:
            CACHE_HITS.labels("extraction").inc()
        else:
            CACHE_MISSES.labels("extraction").inc()
        EXTRACTION_LATENCY.labels(str(payload.browser_used).lower()).observe(time.perf_counter() - started)
        return ExtractionResponse(
            success=payload.status is not ExtractionStatus.FAILED,
            requested_url=url,
            data=payload,
            processing_time_ms=round(elapsed, 2),
            cached=entry.cached,
            stale=entry.stale,
            request_id=request_id,
        )

    async def _extract_once(
        self,
        url: str,
        *,
        include_text: bool,
        include_media: bool,
        allow_browser: bool,
        timeout: float,
    ) -> ExtractionPayload:
        async with self.concurrency.acquire(
            "extract:http",
            self.settings.http_extraction_concurrency,
            timeout=min(timeout, 10.0),
        ):
            fetched = await self.http_fetcher.fetch(
                url,
                timeout=timeout,
                max_bytes=self.settings.http_max_response_bytes,
                require_html=True,
            )
        payload = await self.pipeline.extract_from_html(
            fetched.text,
            fetched.final_url,
            include_text=include_text,
            include_media=include_media,
            browser_used=False,
        )
        if not allow_browser or not self.settings.browser_fallback:
            return payload
        if not self._needs_browser(payload):
            return payload
        try:
            rendered = await self.browser_pool.fetch_html(
                url,
                timeout_ms=int(min(timeout, self.settings.google_navigation_timeout_ms / 1000) * 1000),
                wait_until="domcontentloaded",
                wait_after_load_ms=500,
            )
            browser_payload = await self.pipeline.extract_from_html(
                rendered.html,
                rendered.final_url,
                include_text=include_text,
                include_media=include_media,
                browser_used=True,
            )
        except Exception as exc:
            payload.warnings.append(f"browser fallback was unavailable: {exc.__class__.__name__}")
            return payload
        if self._payload_score(browser_payload) > self._payload_score(payload):
            browser_payload.warnings.extend(payload.warnings)
            browser_payload.warnings.append("browser rendering was used because the static page lacked data")
            return browser_payload
        payload.warnings.append("browser fallback did not add new information")
        return payload

    @staticmethod
    def _needs_browser(payload: ExtractionPayload) -> bool:
        if payload.product is not None and payload.product.price is None:
            return True
        if payload.page.main_text is None and not payload.structured_data:
            return True
        return bool(payload.page.title is None and payload.page.main_text is None)

    @staticmethod
    def _payload_score(payload: ExtractionPayload) -> tuple[int, int, int, int, int]:
        return (
            1 if payload.product is not None and payload.product.price is not None else 0,
            1 if payload.product is not None else 0,
            1 if payload.article is not None else 0,
            len(payload.page.main_text or ""),
            len(payload.structured_data),
        )

    async def extract_batch(
        self,
        urls: list[str],
        *,
        include_text: bool = True,
        include_media: bool = True,
        allow_browser: bool = True,
        use_cache: bool = True,
        force_refresh: bool = False,
        timeout_ms: int | None = None,
        request_id: str | None = None,
    ) -> BatchExtractionResponse:
        started = time.perf_counter()
        timeout = (timeout_ms / 1000) if timeout_ms else self.settings.extraction_timeout

        async def one(url: str) -> ExtractionResponse:
            return await self.extract_url(
                url,
                include_text=include_text,
                include_media=include_media,
                allow_browser=allow_browser,
                use_cache=use_cache,
                force_refresh=force_refresh,
                timeout=timeout,
                request_id=request_id,
            )

        outcomes = await asyncio.gather(*(one(url) for url in urls), return_exceptions=True)
        results: list[ExtractionPayload] = []
        errors: list[ErrorDetail] = []
        for url, outcome in zip(urls, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, AppError):
                    errors.append(ErrorDetail(code=str(outcome.code), message=outcome.message))
                else:
                    logger.warning("batch_extraction_failed", extra={"url": url}, exc_info=outcome)
                    errors.append(
                        ErrorDetail(
                            code="extraction_failed",
                            message="Extraction failed for this URL",
                        )
                    )
                continue
            results.append(outcome.data)
        return BatchExtractionResponse(
            success=not errors,
            results=results,
            errors=errors,
            processing_time_ms=round((time.perf_counter() - started) * 1000, 2),
            request_id=request_id,
        )

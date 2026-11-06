"""Central search orchestrator.

The orchestrator owns the pipeline: validation has already happened at the API
boundary, then normalization, cache policy, concurrency, adapter fan-out,
timeouts, partial results, normalization, deduplication and ranking.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import SQLAlchemyError

from app.adapters.search.base import SearchAdapter, SearchOptions
from app.adapters.search.registry import AdapterRegistry
from app.config import Settings
from app.core.cache import CacheManager, search_cache_key
from app.core.concurrency import CircuitBreaker, ConcurrencyManager, with_retry
from app.core.exceptions import (
    AppError,
    CaptchaDetectedError,
    ConcurrencyTimeoutError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from app.core.logging import get_logger
from app.monitoring.metrics import (
    ADAPTER_FAILURES,
    ADAPTER_SUCCESS,
    CACHE_HITS,
    CACHE_MISSES,
    SEARCH_LATENCY,
)
from app.processors.deduplicate import deduplicate_results
from app.processors.normalize import (
    apply_domain_filters,
    canonical_url,
    domain_from_url,
    normalize_query,
    normalize_url,
)
from app.processors.ranking import RankingEngine
from app.schemas.common import (
    ExtractionStatus,
    SearchMetadata,
    SearchMode,
    SearchType,
    SourceHealthStatus,
    SourceStatus,
)
from app.schemas.search import SearchRequest, SearchResponse, SearchResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.services.extraction_service import ExtractionService

logger = get_logger(__name__)


class SearchOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        registry: AdapterRegistry,
        cache: CacheManager,
        concurrency: ConcurrencyManager,
        ranking: RankingEngine,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        extraction_service: ExtractionService | None = None,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.cache = cache
        self.concurrency = concurrency
        self.ranking = ranking
        self.session_factory = session_factory
        self.extraction_service = extraction_service
        self._breakers: dict[str, CircuitBreaker] = {}

    def _breaker(self, name: str) -> CircuitBreaker:
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name,
                threshold=self.settings.circuit_breaker_threshold,
                cooldown=self.settings.circuit_breaker_cooldown,
                redis_client=self.concurrency.redis_client,
            )
            self._breakers[name] = breaker
        return breaker

    async def search(
        self,
        request: SearchRequest,
        *,
        request_id: str | None = None,
        account_id: str | None = None,
    ) -> SearchResponse:
        started = time.perf_counter()
        query = normalize_query(request.query)
        extract_limit = self._extract_limit(request)
        mode_key = f"{request.mode.value}:{extract_limit}" if request.mode is SearchMode.DEEP else "quick"
        cache_key = search_cache_key(
            query=query,
            country=request.country,
            language=request.language,
            search_type=request.search_type.value,
            limit=request.limit,
            page=request.page,
            sources=request.sources,
            mode=mode_key,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
            safe_search=request.safe_search,
            extract_limit=extract_limit,
        )
        fresh_ttl, stale_ttl = self.settings.cache_ttls(product=request.search_type is SearchType.PRODUCTS)

        async def factory() -> dict[str, Any]:
            response = await self._execute_search(
                request,
                query=query,
                request_id=request_id,
                extract_limit=extract_limit,
            )
            return response.model_dump(mode="json")

        try:
            entry = await self.cache.get_or_set(
                cache_key,
                factory,
                fresh_ttl=fresh_ttl,
                stale_ttl=stale_ttl,
                use_cache=request.use_cache,
                force_refresh=request.force_refresh,
            )
        except Exception:
            raise
        response = SearchResponse.model_validate(entry.value)
        response.metadata.request_id = request_id
        response.metadata.cached = entry.cached
        response.metadata.stale = entry.stale
        response.metadata.processing_time_ms = round((time.perf_counter() - started) * 1000, 2)
        if entry.cached:
            CACHE_HITS.labels("search").inc()
        else:
            CACHE_MISSES.labels("search").inc()
        SEARCH_LATENCY.labels(request.mode.value, request.search_type.value, str(entry.cached).lower()).observe(
            time.perf_counter() - started
        )
        await self._record_history(response, request, account_id=account_id, request_id=request_id)
        return response

    def _extract_limit(self, request: SearchRequest) -> int:
        if request.mode is not SearchMode.DEEP:
            return 0
        if request.extract_limit is not None:
            return request.extract_limit
        return self.settings.deep_search_extract_limit

    def _options(self, request: SearchRequest, query: str, timeout: float) -> SearchOptions:
        return SearchOptions(
            query=query,
            country=request.country,
            language=request.language,
            search_type=request.search_type,
            limit=request.limit,
            page=request.page,
            safe_search=request.safe_search,
            include_domains=request.include_domains,
            exclude_domains=request.exclude_domains,
            timeout_seconds=timeout,
        )

    async def _execute_search(
        self,
        request: SearchRequest,
        *,
        query: str,
        request_id: str | None,
        extract_limit: int,
    ) -> SearchResponse:
        started = time.perf_counter()
        deadline = started + self._deadline_seconds(request)
        adapters = self.registry.select(requested=request.sources, search_type=request.search_type)
        if not adapters:
            raise SourceUnavailableError(
                "no search sources are enabled for the requested search type",
                details={"sources_requested": request.sources or []},
            )

        remaining = max(0.2, deadline - time.monotonic())
        warnings: list[str] = []
        async with self.concurrency.acquire(
            "search:global",
            self.settings.global_search_concurrency,
            timeout=min(remaining, 10.0),
        ):
            options = self._options(request, query, timeout=self.settings.adapter_timeout)
            outcomes = await asyncio.gather(
                *(self._run_adapter(adapter, options, deadline) for adapter in adapters),
                return_exceptions=True,
            )

        statuses: list[SourceStatus] = []
        collected: list[SearchResult] = []
        for adapter, outcome in zip(adapters, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                logger.error(
                    "adapter_unexpected_failure",
                    extra={"adapter": adapter.name},
                    exc_info=outcome,
                )
                statuses.append(
                    SourceStatus(
                        name=adapter.name,
                        status=SourceHealthStatus.UNAVAILABLE,
                        error_code="unexpected_error",
                        error="adapter failed unexpectedly",
                    )
                )
                continue
            results, status = outcome
            statuses.append(status)
            collected.extend(results)

        successful_sources = [status.name for status in statuses if status.status is SourceHealthStatus.OK]
        if not collected and not successful_sources:
            raise SourceUnavailableError(
                "all configured search sources failed",
                details=[status.model_dump(mode="json") for status in statuses],
            )
        if any(not result.title for result in collected):
            warnings.append("one or more results had no title and were discarded")
            collected = [result for result in collected if result.title]

        normalized: list[SearchResult] = []
        for result in collected:
            normalized_url = normalize_url(result.url) or result.url
            canonical = canonical_url(result.url, result.canonical_url)
            domain = result.domain or domain_from_url(normalized_url)
            normalized.append(
                result.model_copy(
                    update={
                        "url": normalized_url,
                        "canonical_url": canonical,
                        "domain": domain,
                    }
                )
            )
        filtered = apply_domain_filters(normalized, request.include_domains, request.exclude_domains)
        deduplicated = deduplicate_results(filtered)
        ranked = self.ranking.rank(deduplicated, query, search_type=request.search_type)
        results = ranked[: request.limit]

        if request.mode is SearchMode.DEEP and extract_limit > 0 and results:
            warnings.extend(await self._deep_extract(results, extract_limit, deadline, request))

        metadata = SearchMetadata(
            query=query,
            country=request.country,
            language=request.language,
            search_type=request.search_type,
            mode=request.mode,
            total_results=len(results),
            processing_time_ms=round((time.perf_counter() - started) * 1000, 2),
            cached=False,
            stale=False,
            page=request.page,
            limit=request.limit,
            sources_used=successful_sources,
            source_statuses=statuses,
            request_id=request_id,
        )
        return SearchResponse(metadata=metadata, results=results, warnings=warnings)

    def _deadline_seconds(self, request: SearchRequest) -> float:
        configured = (
            self.settings.deep_search_timeout if request.mode is SearchMode.DEEP else self.settings.quick_search_timeout
        )
        requested = request.timeout_ms / 1000 if request.timeout_ms else configured
        return min(requested, self.settings.global_request_deadline, configured)

    async def _run_adapter(
        self,
        adapter: SearchAdapter,
        options: SearchOptions,
        deadline: float,
    ) -> tuple[list[SearchResult], SourceStatus]:
        breaker = self._breaker(adapter.name)
        started = time.perf_counter()
        if not await breaker.allow():
            return [], SourceStatus(
                name=adapter.name,
                status=SourceHealthStatus.UNAVAILABLE,
                error_code="circuit_open",
                error="source temporarily disabled after repeated failures",
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return [], SourceStatus(
                name=adapter.name,
                status=SourceHealthStatus.TIMEOUT,
                error_code="request_deadline_exceeded",
                error="request deadline exceeded before adapter execution",
            )
        timeout = max(0.2, min(remaining, self.settings.adapter_timeout, options.timeout_seconds))
        bounded_options = replace(options, timeout_seconds=timeout)

        async def call() -> list[SearchResult]:
            remaining_now = deadline - time.monotonic()
            if remaining_now <= 0.05:
                raise SourceTimeoutError("request deadline exceeded during adapter retry")
            return await asyncio.wait_for(
                adapter.search(bounded_options),
                timeout=max(0.2, min(timeout, remaining_now)),
            )

        try:
            async with self.concurrency.acquire(
                f"search:adapter:{adapter.name}",
                adapter.concurrency_limit,
                timeout=min(timeout, 5.0),
            ):
                results, retried = await with_retry(
                    call,
                    retries=self.settings.search_retries,
                    is_retryable=self._is_retryable,
                )
            await breaker.record_success()
            ADAPTER_SUCCESS.labels(adapter.name).inc()
            return results, SourceStatus(
                name=adapter.name,
                status=SourceHealthStatus.OK,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                result_count=len(results),
                retried=retried,
            )
        except Exception as exc:
            await breaker.record_failure()
            status = self._classify_failure(exc)
            code = exc.code.value if isinstance(exc, AppError) else exc.__class__.__name__
            ADAPTER_FAILURES.labels(adapter.name, code).inc()
            logger.warning(
                "adapter_failed",
                extra={"adapter": adapter.name, "error_code": code},
            )
            return [], SourceStatus(
                name=adapter.name,
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error_code=code,
                error=str(exc)[:500] if str(exc) else exc.__class__.__name__,
            )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return False
        return isinstance(exc, AppError) and exc.retryable

    @staticmethod
    def _classify_failure(exc: Exception) -> SourceHealthStatus:
        if isinstance(exc, (SourceTimeoutError, asyncio.TimeoutError, TimeoutError, ConcurrencyTimeoutError)):
            return SourceHealthStatus.TIMEOUT
        if isinstance(exc, CaptchaDetectedError):
            return SourceHealthStatus.BLOCKED
        if isinstance(exc, SourceUnavailableError):
            return SourceHealthStatus.UNAVAILABLE
        if isinstance(exc, AppError) and exc.code.value == "access_denied":
            return SourceHealthStatus.BLOCKED
        return SourceHealthStatus.UNAVAILABLE

    async def _deep_extract(
        self,
        results: list[SearchResult],
        limit: int,
        deadline: float,
        request: SearchRequest,
    ) -> list[str]:
        if self.extraction_service is None:
            return ["deep extraction is not configured"]
        extraction_service = self.extraction_service
        warnings: list[str] = []
        targets = results[: min(limit, len(results))]

        async def extract(index: int, result: SearchResult) -> str | None:
            remaining = deadline - time.monotonic()
            if remaining <= 0.25:
                result.extraction_status = ExtractionStatus.PENDING
                return f"deep extraction deadline reached for {result.domain or result.url}"
            timeout = max(0.25, min(remaining, self.settings.extraction_timeout))
            try:
                async with self.concurrency.acquire(
                    "deep:extract",
                    self.settings.deep_search_extract_concurrency,
                    timeout=min(timeout, 5.0),
                ):
                    extracted = await asyncio.wait_for(
                        extraction_service.extract_url(
                            result.url,
                            include_text=True,
                            include_media=True,
                            allow_browser=request.mode is SearchMode.DEEP,
                            use_cache=request.use_cache,
                            force_refresh=request.force_refresh,
                            timeout=timeout,
                        ),
                        timeout=timeout + 0.5,
                    )
                result.extraction = extracted.data
                result.extraction_status = extracted.data.status
                return None
            except Exception as exc:
                result.extraction_status = ExtractionStatus.FAILED
                logger.info(
                    "deep_extraction_failed",
                    extra={"url": result.url, "error": str(exc)[:200]},
                )
                return f"extraction failed for {result.domain or result.url}"

        tasks = [extract(index, result) for index, result in enumerate(targets)]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                warnings.append("deep extraction failed unexpectedly")
            elif outcome:
                warnings.append(outcome)
        for result in results[len(targets) :]:
            result.extraction_status = ExtractionStatus.NOT_REQUESTED
        return warnings

    async def _record_history(
        self,
        response: SearchResponse,
        request: SearchRequest,
        *,
        account_id: str | None,
        request_id: str | None,
    ) -> None:
        if self.session_factory is None or account_id is None:
            return
        from uuid import UUID

        from app.models.search_history import SearchHistory

        try:
            async with self.session_factory() as session:
                session.add(
                    SearchHistory(
                        account_id=UUID(account_id),
                        request_id=request_id,
                        query=response.metadata.query,
                        search_type=response.metadata.search_type.value,
                        mode=response.metadata.mode.value,
                        country=response.metadata.country,
                        language=response.metadata.language,
                        result_count=response.metadata.total_results,
                        processing_time_ms=response.metadata.processing_time_ms,
                        cached=response.metadata.cached,
                        sources=response.metadata.sources_used,
                        source_statuses=[
                            status.model_dump(mode="json") for status in response.metadata.source_statuses
                        ],
                    )
                )
                await session.commit()
        except (SQLAlchemyError, ValueError):
            logger.warning("search_history_write_failed", exc_info=True)

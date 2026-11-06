"""Stage A: fast HTTP extraction with SSRF-safe manual redirect handling."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import httpx

from app.config import Settings
from app.core.exceptions import (
    AccessDeniedError,
    ExtractionError,
    InvalidURLError,
    SourceTimeoutError,
    SourceUnavailableError,
    SSRFBlockedError,
)
from app.core.logging import get_logger
from app.core.security import resolve_and_validate

logger = get_logger(__name__)

_REDIRECT_CODES = {301, 302, 303, 307, 308}
_HTML_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "application/xml",
    "text/xml",
    "text/plain",
)


@dataclass(slots=True)
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str | None = None
    elapsed_ms: float = 0.0
    encoding: str | None = None


class HttpFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Callable[[str, float], Awaitable[list[str]]] | None = None,
    ) -> None:
        self.settings = settings
        self._resolver = resolver
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.http_timeout,
                connect=settings.http_connect_timeout,
                read=settings.http_timeout,
                write=settings.http_timeout,
                pool=settings.http_timeout,
            ),
            limits=httpx.Limits(
                max_connections=max(10, settings.http_extraction_concurrency * 2),
                max_keepalive_connections=max(10, settings.http_extraction_concurrency),
                keepalive_expiry=30,
            ),
            headers={
                "User-Agent": settings.http_user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
            },
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def fetch(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_bytes: int | None = None,
        allowed_content_types: tuple[str, ...] | None = None,
        allow_private: bool = False,
        require_html: bool = True,
    ) -> FetchResult:
        started = time.perf_counter()
        limit = max_bytes or self.settings.http_max_response_bytes
        request_headers = dict(headers or {})
        current_url = url
        redirects = 0
        try:
            while True:
                validated = await resolve_and_validate(
                    current_url,
                    allowed_ports=self.settings.http_allowed_ports,
                    allow_private=allow_private or self.settings.ssrf_allow_private,
                    dns_timeout=self.settings.ssrf_dns_timeout,
                    resolver=self._resolver,
                )
                try:
                    async with self._client.stream(
                        "GET",
                        validated.url,
                        headers=request_headers,
                        timeout=timeout or self.settings.http_timeout,
                    ) as response:
                        if response.status_code in _REDIRECT_CODES:
                            location = response.headers.get("location")
                            if not location:
                                raise ExtractionError("redirect response did not include a Location")
                            redirects += 1
                            if redirects > self.settings.http_max_redirects:
                                raise SSRFBlockedError("too many redirects")
                            current_url = urljoin(str(response.url), location)
                            continue
                        if response.status_code in {401, 403}:
                            raise AccessDeniedError(f"upstream source denied access with HTTP {response.status_code}")
                        if response.status_code == 429 or response.status_code >= 500:
                            raise SourceUnavailableError(f"upstream source returned HTTP {response.status_code}")
                        if response.status_code >= 400:
                            raise ExtractionError(f"upstream source returned HTTP {response.status_code}")
                        content_type = response.headers.get("content-type", "").lower()
                        allowed = allowed_content_types or (_HTML_TYPES if require_html else ())
                        if allowed and not any(token in content_type for token in allowed):
                            raise ExtractionError(f"unsupported content type: {content_type or 'unknown'}")
                        chunks: list[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > limit:
                                raise ExtractionError("response exceeded the configured size limit")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        encoding = response.charset_encoding or "utf-8"
                        try:
                            text = body.decode(encoding, errors="replace")
                        except LookupError:
                            encoding = "utf-8"
                            text = body.decode(encoding, errors="replace")
                        return FetchResult(
                            requested_url=url,
                            final_url=str(response.url),
                            status_code=response.status_code,
                            text=text,
                            headers={key.lower(): value for key, value in response.headers.items()},
                            content_type=content_type or None,
                            elapsed_ms=(time.perf_counter() - started) * 1000,
                            encoding=encoding,
                        )
                except (
                    SSRFBlockedError,
                    AccessDeniedError,
                    ExtractionError,
                    SourceUnavailableError,
                ):
                    raise
                except httpx.TimeoutException as exc:
                    raise SourceTimeoutError("upstream request timed out") from exc
                except httpx.InvalidURL as exc:
                    raise InvalidURLError("upstream URL is invalid") from exc
                except httpx.HTTPError as exc:
                    raise ExtractionError("upstream request failed") from exc
        finally:
            try:
                self._client.cookies.clear()
            except Exception:  # pragma: no cover - defensive
                logger.debug("cookie_cleanup_failed", exc_info=True)

    async def fetch_json(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        max_bytes: int | None = None,
    ) -> FetchResult:
        merged = {"Accept": "application/json, text/plain, */*"}
        merged.update(headers or {})
        return await self.fetch(
            url,
            headers=merged,
            timeout=timeout,
            max_bytes=max_bytes,
            require_html=False,
        )

    async def ping(self) -> bool:
        if self._owns_client:
            return True
        return not self._client.is_closed

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"HttpFetcher(owns_client={self._owns_client}, closed={self._client.is_closed})"

    @property
    def metadata(self) -> dict[str, Any]:
        return {"user_agent": self.settings.http_user_agent}

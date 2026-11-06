"""Google SERP collection through a persistent Playwright browser.

No CAPTCHA bypass is attempted, and blocked/denied responses are not retried
aggressively.  Selectors are configurable because search HTML changes.
"""

from __future__ import annotations

import math
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from bs4 import BeautifulSoup, Tag

from app.adapters.search.base import AdapterHealth, SearchAdapter, SearchOptions
from app.config import Settings
from app.core.exceptions import (
    AccessDeniedError,
    CaptchaDetectedError,
    InvalidQueryError,
    SourceTimeoutError,
    SourceUnavailableError,
)
from app.core.logging import get_logger
from app.extractors.base import absolute_url, clean_text
from app.extractors.browser import BrowserPool
from app.processors.normalize import classify_content_type
from app.processors.price import parse_price
from app.schemas.common import SearchType, SourceHealthStatus
from app.schemas.product import PriceData, PriceSource
from app.schemas.search import SearchResult

logger = get_logger(__name__)

DEFAULT_SELECTORS: dict[str, Any] = {
    "result_container": "div.g",
    "snippet": "div.VwiC3b, span.aCOpRe, div[data-sncf], div.IsZvec",
    "thumbnail": "img",
    "captcha_markers": [
        "unusual traffic",
        "not a robot",
        "recaptcha",
        "automated queries",
        "verify you are human",
    ],
    "blocked_markers": [
        "access denied",
        "request blocked",
        "you have exceeded",
        "unavailable for legal reasons",
    ],
}

_GOOGLE_HOST = "google.com"


def _unwrap_google_url(href: str) -> str | None:
    try:
        parts = urlsplit(href)
    except ValueError:
        return None
    host = (parts.hostname or "").casefold()
    is_google = host == _GOOGLE_HOST or host.endswith(f".{_GOOGLE_HOST}")
    if is_google and parts.path in {"/url", "/url/"}:
        query = parse_qs(parts.query)
        target = query.get("q") or query.get("url")
        if target:
            return target[0]
        return None
    if is_google:
        return None
    if parts.scheme not in {"http", "https"}:
        return None
    return href


def detect_google_block(url: str, html: str, selectors: dict[str, Any]) -> None:
    lowered_url = url.casefold()
    if "/sorry/" in lowered_url or "consent.google." in lowered_url:
        raise CaptchaDetectedError("Google returned a CAPTCHA or consent challenge")
    merged = {**DEFAULT_SELECTORS, **selectors}
    sample = html[:50_000].casefold()
    for marker in merged.get("captcha_markers", []):
        if marker in sample:
            raise CaptchaDetectedError("Google presented an anti-bot challenge")
    for marker in merged.get("blocked_markers", []):
        if marker in sample:
            raise AccessDeniedError("Google denied automated access")


def parse_google_results(
    html: str,
    final_url: str,
    *,
    limit: int,
    selectors: dict[str, Any] | None = None,
) -> list[SearchResult]:
    selectors = {**DEFAULT_SELECTORS, **(selectors or {})}
    soup = BeautifulSoup(html, "lxml")
    results: list[SearchResult] = []
    seen: set[str] = set()

    anchors = soup.select("a:has(h3)")
    for anchor in anchors:
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        target = _unwrap_google_url(href)
        if not target:
            continue
        absolute = absolute_url(final_url, target)
        if not absolute or absolute in seen:
            continue
        title_tag = anchor.find("h3")
        title = clean_text(title_tag.get_text(" ", strip=True)) if isinstance(title_tag, Tag) else None
        if not title:
            continue
        container = anchor.find_parent(["div", "li"])
        snippet = None
        thumbnail = None
        if isinstance(container, Tag):
            for selector in str(selectors.get("snippet", "")).split(","):
                selector = selector.strip()
                if not selector:
                    continue
                snippet_tag = container.select_one(selector)
                if isinstance(snippet_tag, Tag):
                    snippet = clean_text(snippet_tag.get_text(" ", strip=True))
                    break
            if not snippet:
                raw = clean_text(container.get_text(" ", strip=True)) or ""
                snippet = clean_text(raw.replace(title, "", 1)) if title else raw
            image_tag = container.select_one(str(selectors.get("thumbnail", "img")))
            if isinstance(image_tag, Tag):
                src = image_tag.get("src") or image_tag.get("data-src")
                if isinstance(src, str):
                    thumbnail = absolute_url(final_url, src)
        seen.add(absolute)
        snippet_price = None
        if snippet:
            parsed = parse_price(snippet)
            if parsed and parsed.currency:
                snippet_price = PriceData(
                    amount=parsed.amount,
                    currency=parsed.currency,
                    price_source=PriceSource.SEARCH_SNIPPET,
                    verified=False,
                )
        results.append(
            SearchResult(
                position=len(results) + 1,
                title=title,
                url=absolute,
                canonical_url=absolute,
                domain=urlsplit(absolute).hostname,
                snippet=snippet[:1000] if snippet else None,
                content_type=classify_content_type(absolute, title=title, snippet=snippet),
                source="google",
                thumbnail=thumbnail,
                snippet_price=snippet_price,
            )
        )
        if len(results) >= limit:
            break

    if results:
        return results

    for container in soup.select(str(selectors.get("result_container", "div.g"))):
        if not isinstance(container, Tag):
            continue
        result_anchor = container.find("a", href=True)
        result_title_tag = container.find("h3")
        if not isinstance(result_anchor, Tag) or not isinstance(result_title_tag, Tag):
            continue
        href = result_anchor.get("href")
        target = _unwrap_google_url(str(href)) if href else None
        absolute = absolute_url(final_url, target) if target else None
        title = clean_text(result_title_tag.get_text(" ", strip=True))
        if not absolute or not title or absolute in seen:
            continue
        seen.add(absolute)
        snippet = None
        for selector in str(selectors.get("snippet", "")).split(","):
            selector = selector.strip()
            if not selector:
                continue
            snippet_tag = container.select_one(selector)
            if isinstance(snippet_tag, Tag):
                snippet = clean_text(snippet_tag.get_text(" ", strip=True))
                break
        results.append(
            SearchResult(
                position=len(results) + 1,
                title=title,
                url=absolute,
                canonical_url=absolute,
                domain=urlsplit(absolute).hostname,
                snippet=snippet,
                content_type=classify_content_type(absolute, title=title, snippet=snippet),
                source="google",
            )
        )
        if len(results) >= limit:
            break
    return results


class GoogleBrowserAdapter(SearchAdapter):
    name = "google"
    adapter_type = "google_browser"
    capabilities = (SearchType.WEB, SearchType.PRODUCTS, SearchType.NEWS)
    requires_browser = True
    description = "Google organic results rendered by the persistent browser pool"

    def __init__(self, *, settings: Settings, browser_pool: BrowserPool) -> None:
        self.settings = settings
        self.browser_pool = browser_pool
        self.selectors = {**DEFAULT_SELECTORS, **settings.google_selectors}
        self.concurrency_limit = settings.per_adapter_concurrency

    def _build_url(self, options: SearchOptions, start: int) -> str:
        params: dict[str, str] = {
            "q": options.query,
            "num": str(min(options.limit, self.settings.google_results_per_page)),
            "hl": options.language,
            "gl": options.country,
            "start": str(start),
        }
        if options.safe_search:
            params["safe"] = "active"
        if options.search_type is SearchType.PRODUCTS:
            params["tbm"] = "shop"
        elif options.search_type is SearchType.NEWS:
            params["tbm"] = "nws"
        return f"{self.settings.google_base_url.rstrip('/')}/search?{urlencode(params)}"

    async def search(self, options: SearchOptions) -> list[SearchResult]:
        if not options.query.strip():
            raise InvalidQueryError()
        started = time.perf_counter()
        timeout_ms = int(min(options.timeout_seconds, self.settings.adapter_timeout) * 1000)
        results: list[SearchResult] = []
        pages = max(
            1,
            min(
                self.settings.google_max_pages,
                math.ceil(options.limit / max(1, self.settings.google_results_per_page)),
            ),
        )
        per_page = min(options.limit, self.settings.google_results_per_page)
        seen: set[str] = set()
        for page_index in range(pages):
            remaining_ms = timeout_ms - int((time.perf_counter() - started) * 1000)
            if remaining_ms <= 250:
                break
            start = (
                options.page - 1
            ) * self.settings.google_results_per_page + page_index * self.settings.google_results_per_page
            url = self._build_url(options, start)
            try:
                rendered = await self.browser_pool.fetch_html(
                    url,
                    timeout_ms=remaining_ms,
                    wait_until="domcontentloaded",
                    wait_after_load_ms=250,
                )
            except (CaptchaDetectedError, AccessDeniedError):
                raise
            except SourceUnavailableError:
                raise
            except Exception as exc:
                raise SourceTimeoutError("Google browser navigation failed") from exc
            detect_google_block(rendered.final_url, rendered.html, self.selectors)
            parsed = parse_google_results(
                rendered.html,
                rendered.final_url,
                limit=per_page,
                selectors=self.selectors,
            )
            for result in parsed:
                if result.url in seen:
                    continue
                seen.add(result.url)
                result.position = len(results) + 1
                result.source = self.name
                results.append(result)
            if len(results) >= options.limit or len(parsed) < per_page:
                break
        logger.info(
            "google_search_completed",
            extra={"result_count": len(results), "query_length": len(options.query)},
        )
        return results[: options.limit]

    async def health_check(self) -> AdapterHealth:
        started = time.perf_counter()
        try:
            healthy, detail = await self.browser_pool.health_check()
        except Exception as exc:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.UNAVAILABLE,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc),
            )
        return AdapterHealth(
            name=self.name,
            status=SourceHealthStatus.OK if healthy else SourceHealthStatus.UNAVAILABLE,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=detail,
        )

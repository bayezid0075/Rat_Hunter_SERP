"""Generic website search plugin.

Site-specific knowledge lives here (URL template and selectors), not in the
orchestrator.  Plugins perform real HTTP requests through the shared,
SSRF-guarded fetcher and parse only what is actually present.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from urllib.parse import quote_plus, urlencode, urlsplit

from bs4 import BeautifulSoup, Tag

from app.adapters.search.base import AdapterHealth, SearchOptions
from app.core.exceptions import AccessDeniedError, CaptchaDetectedError
from app.core.logging import get_logger
from app.extractors.base import absolute_url, clean_text
from app.extractors.http import HttpFetcher
from app.processors.price import build_price
from app.schemas.common import ContentType, SourceHealthStatus
from app.schemas.product import PriceSource
from app.schemas.search import SearchResult

logger = get_logger(__name__)

_BLOCK_MARKERS = {
    "captcha": ("captcha", "recaptcha", "just a moment", "robot check", "verify you are human"),
    "denied": ("access denied", "request blocked", "forbidden", "attention required", "cf-chl"),
}


def detect_block(html: str) -> None:
    sample = html[:40_000].casefold()
    for marker in _BLOCK_MARKERS["captcha"]:
        if marker in sample:
            raise CaptchaDetectedError("the website presented a challenge")
    for marker in _BLOCK_MARKERS["denied"]:
        if marker in sample:
            raise AccessDeniedError("the website denied automated access")


class WebsiteSearchPlugin(ABC):
    name: str
    display_name: str
    base_url: str
    search_path: str
    currency_hint: str | None = None
    requires_js: bool = False
    card_selectors: tuple[str, ...] = ()
    title_selectors: tuple[str, ...] = ()
    price_selectors: tuple[str, ...] = ()
    image_selectors: tuple[str, ...] = ()

    def build_search_url(self, query: str, options: SearchOptions) -> str:
        path = self.search_path.format(query=quote_plus(query))
        url = f"{self.base_url.rstrip('/')}{path}"
        if options.page > 1:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode({'page': options.page})}"
        return url

    @abstractmethod
    def parse(self, html: str, final_url: str, *, limit: int, adapter_name: str) -> list[SearchResult]:
        """Parse a real search response into standardized results."""

    async def fetch_search(self, options: SearchOptions, fetcher: HttpFetcher) -> tuple[str, str]:
        url = self.build_search_url(options.query, options)
        response = await fetcher.fetch(
            url,
            headers={
                "Accept-Language": f"{options.language},en;q=0.7",
                "Referer": f"{self.base_url.rstrip('/')}/",
            },
            timeout=min(options.timeout_seconds, 12.0),
            require_html=True,
        )
        detect_block(response.text)
        return response.text, response.final_url

    async def health_check(self, fetcher: HttpFetcher) -> AdapterHealth:
        started = time.perf_counter()
        try:
            response = await fetcher.fetch(
                f"{self.base_url.rstrip('/')}/",
                timeout=5.0,
                require_html=True,
            )
            detect_block(response.text)
        except CaptchaDetectedError:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.BLOCKED,
                latency_ms=(time.perf_counter() - started) * 1000,
                error="challenge page detected",
            )
        except AccessDeniedError:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.BLOCKED,
                latency_ms=(time.perf_counter() - started) * 1000,
                error="access denied",
            )
        except Exception as exc:
            return AdapterHealth(
                name=self.name,
                status=SourceHealthStatus.UNAVAILABLE,
                latency_ms=(time.perf_counter() - started) * 1000,
                error=str(exc) or exc.__class__.__name__,
            )
        return AdapterHealth(
            name=self.name,
            status=SourceHealthStatus.OK,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def _extract_card(self, card: Tag, base_url: str) -> tuple[str, str, str | None, str | None, str | None]:
        anchor = card if card.name == "a" and card.get("href") else card.find("a", href=True)
        if not isinstance(anchor, Tag):
            return "", "", None, None, None
        href = anchor.get("href")
        if not isinstance(href, str):
            return "", "", None, None, None
        url = absolute_url(base_url, href)
        if not url:
            return "", "", None, None, None
        title = None
        for selector in self.title_selectors:
            tag = card.select_one(selector)
            if isinstance(tag, Tag):
                title = clean_text(tag.get_text(" ", strip=True))
                if title:
                    break
        if not title:
            title = clean_text(anchor.get_text(" ", strip=True))
        if not title:
            return "", "", None, None, None
        price_text = None
        for selector in self.price_selectors:
            tag = card.select_one(selector)
            if isinstance(tag, Tag):
                price_text = clean_text(tag.get_text(" ", strip=True))
                if price_text:
                    break
        image = None
        for selector in self.image_selectors or ("img",):
            tag = card.select_one(selector)
            if isinstance(tag, Tag):
                src = tag.get("src") or tag.get("data-src")
                if isinstance(src, str):
                    image = absolute_url(base_url, src)
                    if image:
                        break
        text = clean_text(card.get_text(" ", strip=True)) or ""
        snippet = text[:400] if text else None
        return title, url, price_text, image, snippet

    def _from_cards(self, soup: BeautifulSoup, final_url: str, *, limit: int, adapter_name: str) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        for selector in self.card_selectors:
            for card in soup.select(selector):
                if not isinstance(card, Tag):
                    continue
                title, url, price_text, image, snippet = self._extract_card(card, final_url)
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                price = (
                    build_price(
                        price_text,
                        source=PriceSource.SEARCH_SNIPPET,
                        default_currency=self.currency_hint,
                        verified=False,
                    )
                    if price_text
                    else None
                )
                results.append(
                    SearchResult(
                        position=len(results) + 1,
                        title=title,
                        url=url,
                        canonical_url=url,
                        domain=urlsplit(url).hostname,
                        snippet=snippet,
                        content_type=ContentType.PRODUCT,
                        source=adapter_name,
                        thumbnail=image,
                        snippet_price=price,
                    )
                )
                if len(results) >= limit:
                    return results
        return results

    def _fallback_links(
        self, soup: BeautifulSoup, final_url: str, *, limit: int, adapter_name: str
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        product_hints = ("/product", "/products/", "/p/", "-p-", "/item/", "/dp/")
        for anchor in soup.find_all("a", href=True):
            if not isinstance(anchor, Tag):
                continue
            href = str(anchor.get("href"))
            if not any(hint in href.casefold() for hint in product_hints):
                continue
            url = absolute_url(final_url, href)
            title = clean_text(anchor.get_text(" ", strip=True))
            if not url or not title or len(title) < 8 or url in seen:
                continue
            seen.add(url)
            results.append(
                SearchResult(
                    position=len(results) + 1,
                    title=title,
                    url=url,
                    canonical_url=url,
                    domain=urlsplit(url).hostname,
                    content_type=ContentType.PRODUCT,
                    source=adapter_name,
                )
            )
            if len(results) >= limit:
                break
        return results


class ConfigurablePlugin(WebsiteSearchPlugin):
    """Concrete plugin whose behaviour is entirely declared by class metadata."""

    def parse(self, html: str, final_url: str, *, limit: int, adapter_name: str) -> list[SearchResult]:
        soup = BeautifulSoup(html, "lxml")
        results = self._from_cards(soup, final_url, limit=limit, adapter_name=adapter_name)
        if results:
            return results
        return self._fallback_links(soup, final_url, limit=limit, adapter_name=adapter_name)

"""Stage B: persistent Playwright browser extraction.

Browsers are launched lazily, kept alive across requests, and reused.  Each
acquisition creates an isolated browser context (fresh cookies/storage) and a
page, which is closed after use.  A crashed browser is restarted on demand.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.core.exceptions import (
    AccessDeniedError,
    BrowserUnavailableError,
    InvalidURLError,
    SourceUnavailableError,
    SSRFBlockedError,
)
from app.core.logging import get_logger
from app.core.security import (
    resolve_and_validate,
    validate_url_syntax,
)
from app.monitoring.metrics import ACTIVE_BROWSER_PAGES, BROWSER_INSTANCES

logger = get_logger(__name__)


@dataclass(slots=True)
class RenderedPage:
    requested_url: str
    final_url: str
    html: str
    status_code: int | None
    elapsed_ms: float


class _BrowserInstance:
    def __init__(self, playwright: Any, settings: Settings, index: int) -> None:
        self.playwright = playwright
        self.settings = settings
        self.index = index
        self.browser: Any = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        try:
            return self.browser is not None and bool(self.browser.is_connected())
        except Exception:
            return False

    async def ensure_started(self) -> Any:
        async with self._lock:
            if self.connected:
                return self.browser
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception:
                    logger.debug("browser_close_stale_failed", exc_info=True)
                self.browser = None
            try:
                self.browser = await self.playwright.chromium.launch(
                    headless=self.settings.google_headless,
                    args=["--disable-dev-shm-usage", "--no-first-run", "--disable-gpu"],
                )
            except Exception as exc:
                raise BrowserUnavailableError(
                    "Playwright Chromium could not be launched; run `playwright install chromium`"
                ) from exc
            return self.browser

    async def close(self) -> None:
        async with self._lock:
            if self.browser is not None:
                try:
                    await self.browser.close()
                except Exception:
                    logger.debug("browser_close_failed", exc_info=True)
                self.browser = None


class BrowserPool:
    def __init__(
        self,
        settings: Settings,
        *,
        instances: int | None = None,
        pages_per_instance: int | None = None,
    ) -> None:
        self.settings = settings
        self.instances = instances or settings.browser_instances
        self.pages_per_instance = pages_per_instance or settings.browser_pages_per_instance
        self._playwright: Any = None
        self._browsers: list[_BrowserInstance] = []
        self._semaphore = asyncio.Semaphore(max(1, self.instances * self.pages_per_instance))
        self._start_lock = asyncio.Lock()
        self._rr = 0
        self._started = False

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - optional install
                raise BrowserUnavailableError("Playwright is not installed") from exc
            try:
                self._playwright = await async_playwright().start()
                self._browsers = [
                    _BrowserInstance(self._playwright, self.settings, index) for index in range(self.instances)
                ]
                for browser in self._browsers:
                    await browser.ensure_started()
            except BrowserUnavailableError:
                await self._teardown_partial()
                raise
            except Exception as exc:
                await self._teardown_partial()
                raise BrowserUnavailableError("Playwright Chromium could not be started") from exc
            self._started = True
            BROWSER_INSTANCES.set(len(self._browsers))

    async def _teardown_partial(self) -> None:
        for browser in self._browsers:
            await browser.close()
        self._browsers = []
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("playwright_stop_failed", exc_info=True)
            self._playwright = None
        BROWSER_INSTANCES.set(0)

    async def _pick(self) -> _BrowserInstance:
        if not self._started or not self._browsers:
            await self.start()
        self._rr = (self._rr + 1) % max(1, len(self._browsers))
        instance = self._browsers[self._rr]
        await instance.ensure_started()
        return instance

    @asynccontextmanager
    async def acquire_page(self, *, timeout: float = 15.0) -> AsyncIterator[Any]:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise BrowserUnavailableError("no browser page slot became available") from exc
        page: Any = None
        context: Any = None
        ACTIVE_BROWSER_PAGES.inc()
        try:
            instance = await self._pick()
            context = await instance.browser.new_context(
                locale=self.settings.google_locale,
                user_agent=self.settings.http_user_agent,
                ignore_https_errors=False,
                java_script_enabled=True,
            )
            page = await context.new_page()
            page.set_default_navigation_timeout(self.settings.google_navigation_timeout_ms)
            page.set_default_timeout(self.settings.google_timeout_ms)
            await page.route("**/*", self._route_guard)
            yield page
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    logger.debug("page_close_failed", exc_info=True)
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    logger.debug("context_close_failed", exc_info=True)
            ACTIVE_BROWSER_PAGES.dec()
            self._semaphore.release()

    async def _route_guard(self, route: Any, request: Any) -> None:
        url = request.url
        if url.startswith(("data:", "blob:", "about:")):
            await route.continue_()
            return
        try:
            validate_url_syntax(
                url,
                allowed_ports=self.settings.http_allowed_ports,
                allow_private=self.settings.ssrf_allow_private,
            )
            if request.resource_type == "document":
                await resolve_and_validate(
                    url,
                    allowed_ports=self.settings.http_allowed_ports,
                    allow_private=self.settings.ssrf_allow_private,
                    dns_timeout=self.settings.ssrf_dns_timeout,
                )
        except (InvalidURLError, SSRFBlockedError):
            logger.warning("browser_blocked_request", extra={"blocked_url": url.split("?")[0]})
            await route.abort()
            return
        await route.continue_()

    async def fetch_html(
        self,
        url: str,
        *,
        timeout_ms: int | None = None,
        wait_until: str = "domcontentloaded",
        wait_after_load_ms: int = 0,
        extra_headers: dict[str, str] | None = None,
    ) -> RenderedPage:
        await resolve_and_validate(
            url,
            allowed_ports=self.settings.http_allowed_ports,
            allow_private=self.settings.ssrf_allow_private,
            dns_timeout=self.settings.ssrf_dns_timeout,
        )
        started = time.perf_counter()
        attempts = 2
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self.acquire_page(timeout=(timeout_ms or 15_000) / 1000) as page:
                    if extra_headers:
                        await page.set_extra_http_headers(extra_headers)
                    response = await page.goto(
                        url,
                        wait_until=wait_until,
                        timeout=timeout_ms or self.settings.google_navigation_timeout_ms,
                    )
                    if wait_after_load_ms:
                        await page.wait_for_timeout(wait_after_load_ms)
                    html = await page.content()
                    final_url = page.url
                    status = response.status if response is not None else None
                    if status in {401, 403}:
                        raise AccessDeniedError(f"upstream source denied access with HTTP {status}")
                    if status == 429 or (status is not None and status >= 500):
                        raise SourceUnavailableError(f"upstream source returned HTTP {status}")
                    return RenderedPage(
                        requested_url=url,
                        final_url=final_url,
                        html=html,
                        status_code=status,
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                    )
            except (AccessDeniedError, SourceUnavailableError, BrowserUnavailableError):
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    logger.warning("browser_navigation_retry", extra={"url": url}, exc_info=True)
                    continue
                break
        raise BrowserUnavailableError("browser navigation failed") from last_error

    async def health_check(self) -> tuple[bool, str | None]:
        try:
            await asyncio.wait_for(self.start(), timeout=10)
        except Exception as exc:
            return False, str(exc)
        for instance in self._browsers:
            if not instance.connected:
                return False, "a browser instance is disconnected"
        return True, None

    async def close(self) -> None:
        for instance in self._browsers:
            await instance.close()
        self._browsers = []
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                logger.debug("playwright_stop_failed", exc_info=True)
            self._playwright = None
        self._started = False
        BROWSER_INSTANCES.set(0)
        ACTIVE_BROWSER_PAGES.set(0)

"""Browser pool lifecycle tests using a fake Playwright implementation."""

from __future__ import annotations

from typing import Any

import pytest
from app.config import Settings
from app.extractors.browser import BrowserPool


class FakePage:
    def __init__(self) -> None:
        self.url = "https://example.com/"
        self.closed = False

    def set_default_navigation_timeout(self, _: int) -> None:
        pass

    def set_default_timeout(self, _: int) -> None:
        pass

    async def route(self, _: str, __: Any) -> None:
        pass

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self) -> None:
        self.page = FakePage()
        self.closed = False

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    connected = True

    def __init__(self) -> None:
        self.contexts: list[FakeContext] = []
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected and not self.closed

    async def new_context(self, **_: Any) -> FakeContext:
        context = FakeContext()
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakeChromium:
    def __init__(self) -> None:
        self.browsers: list[FakeBrowser] = []

    async def launch(self, **_: Any) -> FakeBrowser:
        browser = FakeBrowser()
        self.browsers.append(browser)
        return browser


class FakePlaywright:
    def __init__(self) -> None:
        self.chromium = FakeChromium()
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightManager:
    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


@pytest.mark.asyncio
async def test_browser_pool_starts_reuses_and_restarts(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    playwright = FakePlaywright()
    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: FakePlaywrightManager(playwright),
    )
    pool = BrowserPool(settings, instances=1, pages_per_instance=1)
    await pool.start()

    assert await pool.health_check() == (True, None)
    async with pool.acquire_page(timeout=1) as page:
        assert page.url == "https://example.com/"
    context = playwright.chromium.browsers[0].contexts[0]
    assert context.closed is True
    assert context.page.closed is True

    first_browser = playwright.chromium.browsers[0]
    first_browser.connected = False
    async with pool.acquire_page(timeout=1):
        pass
    assert playwright.chromium.browsers[-1] is not first_browser
    assert pool._browsers[0].browser is playwright.chromium.browsers[-1]
    assert len(playwright.chromium.browsers) == 2

    await pool.close()
    assert playwright.stopped is True
    assert pool._started is False


@pytest.mark.asyncio
async def test_browser_route_guard_blocks_private_targets(settings: Settings) -> None:
    class Route:
        def __init__(self) -> None:
            self.action: str | None = None

        async def continue_(self) -> None:
            self.action = "continue"

        async def abort(self) -> None:
            self.action = "abort"

    class Request:
        def __init__(self, url: str, resource_type: str = "document") -> None:
            self.url = url
            self.resource_type = resource_type

    pool = BrowserPool(settings)
    blocked = Route()
    await pool._route_guard(blocked, Request("http://127.0.0.1/private"))
    assert blocked.action == "abort"

    allowed = Route()
    await pool._route_guard(allowed, Request("data:text/html,hello", resource_type="document"))
    assert allowed.action == "continue"

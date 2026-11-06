from __future__ import annotations

import httpx
import pytest
from app.config import Settings
from app.core.exceptions import ExtractionError, SSRFBlockedError
from app.extractors.http import HttpFetcher


def _fetcher(handler: object, *, max_bytes: int = 5_000_000) -> HttpFetcher:
    settings = Settings(
        environment="test",
        http_max_response_bytes=max_bytes,
        ssrf_dns_timeout=1.0,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpFetcher(settings, client=client, resolver=_public_resolver)


async def _public_resolver(_: str, __: float) -> list[str]:
    return ["93.184.216.34"]


async def test_redirect_to_private_address_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/internal"},
            request=request,
        )

    fetcher = _fetcher(handler)
    with pytest.raises(SSRFBlockedError):
        await fetcher.fetch("https://public.example/start")
    await fetcher.client.aclose()


async def test_redirect_to_metadata_ip_is_blocked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
            request=request,
        )

    fetcher = _fetcher(handler)
    with pytest.raises(SSRFBlockedError):
        await fetcher.fetch("https://public.example/start")
    await fetcher.client.aclose()


async def test_response_size_limit_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html>" + b"x" * 200,
            request=request,
        )

    fetcher = _fetcher(handler, max_bytes=50)
    with pytest.raises(ExtractionError):
        await fetcher.fetch("https://public.example/large")
    await fetcher.client.aclose()


async def test_non_html_response_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF-1.7",
            request=request,
        )

    fetcher = _fetcher(handler)
    with pytest.raises(ExtractionError):
        await fetcher.fetch("https://public.example/file.pdf")
    await fetcher.client.aclose()


async def test_client_credentials_and_unusual_ports_rejected() -> None:
    fetcher = _fetcher(lambda request: httpx.Response(200, request=request))
    with pytest.raises(SSRFBlockedError):
        await fetcher.fetch("https://user:pass@public.example/")
    with pytest.raises(SSRFBlockedError):
        await fetcher.fetch("https://public.example:8443/")
    await fetcher.client.aclose()

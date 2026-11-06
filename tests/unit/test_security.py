from __future__ import annotations

import pytest
from app.core.exceptions import InvalidURLError, SSRFBlockedError
from app.core.security import (
    generate_api_key,
    hash_api_key,
    is_safe_redirect,
    normalize_url,
    resolve_and_validate,
    validate_url_syntax,
    verify_api_key,
)


def test_validate_url_syntax_normalizes() -> None:
    normalized, host, port = validate_url_syntax("HTTPS://Example.COM:443/a/../b?x=1#frag")
    assert normalized == "https://example.com/a/../b?x=1"
    assert host == "example.com"
    assert port == 443


def test_normalize_url_drops_default_port() -> None:
    assert normalize_url("https://example.com:443/path") == "https://example.com/path"
    assert normalize_url("http://example.com:80/path") == "http://example.com/path"
    assert normalize_url("https://example.com:8443/path") == "https://example.com:8443/path"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https://user:pass@example.com/",
        "http://localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/",
        "https://example.com:8080/",
    ],
)
def test_validate_url_syntax_rejects_unsafe_targets(url: str) -> None:
    with pytest.raises((InvalidURLError, SSRFBlockedError)):
        validate_url_syntax(url)


def test_private_targets_allowed_only_when_configured() -> None:
    normalized, host, port = validate_url_syntax("http://127.0.0.1:8080/x", allowed_ports=[8080], allow_private=True)
    assert normalized == "http://127.0.0.1:8080/x"
    assert host == "127.0.0.1"
    assert port == 8080


async def test_resolve_and_validate_public_addresses() -> None:
    async def resolver(_: str, __: float) -> list[str]:
        return ["93.184.216.34"]

    target = await resolve_and_validate("https://example.com/path", resolver=resolver)
    assert target.addresses == ("93.184.216.34",)
    assert target.url == "https://example.com/path"


async def test_resolve_and_validate_rejects_private_dns() -> None:
    async def resolver(_: str, __: float) -> list[str]:
        return ["10.0.0.7"]

    with pytest.raises(SSRFBlockedError):
        await resolve_and_validate("https://internal.example/", resolver=resolver)


async def test_resolve_and_validate_rejects_empty_dns() -> None:
    async def resolver(_: str, __: float) -> list[str]:
        return []

    with pytest.raises(InvalidURLError):
        await resolve_and_validate("https://missing.example/", resolver=resolver)


def test_safe_redirect_matches_host_only() -> None:
    assert is_safe_redirect("https://example.com/other", "example.com")
    assert not is_safe_redirect("https://other.example/", "example.com")
    assert not is_safe_redirect("http://127.0.0.1/", "example.com")


def test_api_key_hash_roundtrip() -> None:
    raw, prefix, _ = generate_api_key("sk_test")
    digest = hash_api_key(raw, "pepper")
    assert raw.startswith("sk_test_")
    assert prefix == raw[:16]
    assert verify_api_key(raw, digest, "pepper")
    assert not verify_api_key(raw + "x", digest, "pepper")


def test_hash_changes_with_pepper() -> None:
    assert hash_api_key("sk_test_value", "a") != hash_api_key("sk_test_value", "b")
    assert len(hash_api_key("sk_test_value", "a")) == 64

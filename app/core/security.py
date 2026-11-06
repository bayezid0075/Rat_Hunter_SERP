"""API key primitives and SSRF protection.

SSRF defence is layered:

* syntax validation (scheme, credentials, port allow-list);
* hostname block-list for well known internal names;
* DNS resolution with every resolved address checked against private,
  loopback, link-local, reserved, multicast and metadata ranges;
* redirect targets revalidated by the HTTP extractor on every hop;
* the browser extractor installs a request interceptor with the same rules;
* documentation recommends network-level egress restrictions as the final
  boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import secrets
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from app.core.exceptions import InvalidURLError, SSRFBlockedError

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "kubernetes.default",
    "kubernetes.default.svc",
}
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")
_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("100.100.100.200"),
    ipaddress.ip_address("fd00:ec2::254"),
}


def generate_api_key(prefix: str = "sk_live") -> tuple[str, str, str]:
    """Return ``(raw_key, display_prefix, hash)``.

    The raw key is shown exactly once to the caller.  Only the HMAC hash is
    persisted.
    """

    token = secrets.token_urlsafe(32)
    raw = f"{prefix}_{token}"
    return raw, raw[:16], hash_api_key(raw)


def hash_api_key(raw_key: str, pepper: str | None = None) -> str:
    if pepper is None:
        from app.config import get_settings

        pepper = get_settings().api_key_pepper
    return hmac.new(pepper.encode("utf-8"), raw_key.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_api_key(raw_key: str, expected_hash: str, pepper: str | None = None) -> bool:
    candidate = hash_api_key(raw_key, pepper)
    return hmac.compare_digest(candidate, expected_hash)


@dataclass(frozen=True, slots=True)
class ValidatedTarget:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    netloc = host
    if parts.port and not (
        (parts.scheme == "http" and parts.port == 80) or (parts.scheme == "https" and parts.port == 443)
    ):
        netloc = f"{host}:{parts.port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


def _is_blocked_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return (
        address in _METADATA_IPS
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or not address.is_global
    )


def _check_hostname(host: str) -> None:
    lowered = host.rstrip(".").lower()
    if not lowered:
        raise InvalidURLError("URL must include a hostname")
    if lowered in _BLOCKED_HOSTNAMES or any(lowered.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        raise SSRFBlockedError("requests to internal hostnames are not allowed")
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return
    if _is_blocked_ip(ip):
        raise SSRFBlockedError("requests to private or reserved addresses are not allowed")


def validate_url_syntax(
    url: str,
    *,
    allowed_ports: tuple[int, ...] | list[int] = (80, 443),
    allow_private: bool = False,
) -> tuple[str, str, int]:
    if not isinstance(url, str) or not url.strip():
        raise InvalidURLError("URL is empty")
    candidate = url.strip()
    if len(candidate) > 2048:
        raise InvalidURLError("URL is too long")
    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise InvalidURLError("only http and https URLs are supported")
    if parts.username or parts.password:
        raise SSRFBlockedError("credentials in URLs are not allowed")
    host = parts.hostname
    if not host:
        raise InvalidURLError("URL must include a hostname")
    try:
        port = parts.port
    except ValueError as exc:
        raise InvalidURLError("URL contains an invalid port") from exc
    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in set(allowed_ports):
        raise SSRFBlockedError(f"port {effective_port} is not allowed")
    if not allow_private:
        _check_hostname(host)
    return normalize_url(candidate), host, effective_port


async def _default_resolve(host: str, timeout: float = 3.0) -> list[str]:
    loop = asyncio.get_running_loop()

    def _lookup() -> list[str]:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        return sorted({str(info[4][0]) for info in infos})

    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _lookup), timeout=timeout)
    except TimeoutError as exc:
        raise SSRFBlockedError("DNS resolution timed out") from exc
    except socket.gaierror as exc:
        raise InvalidURLError("hostname could not be resolved") from exc


async def resolve_and_validate(
    url: str,
    *,
    allowed_ports: tuple[int, ...] | list[int] = (80, 443),
    allow_private: bool = False,
    dns_timeout: float = 3.0,
    resolver: Callable[[str, float], Awaitable[list[str]]] | None = None,
) -> ValidatedTarget:
    normalized, host, port = validate_url_syntax(url, allowed_ports=allowed_ports, allow_private=allow_private)
    if allow_private:
        return ValidatedTarget(normalized, "https" if port == 443 else "http", host, port, ())
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal):
            raise SSRFBlockedError("requests to private or reserved addresses are not allowed")
        return ValidatedTarget(normalized, normalized.split(":", 1)[0], host, port, (str(literal),))

    resolve = resolver or _default_resolve
    addresses = await resolve(host, dns_timeout)
    if not addresses:
        raise InvalidURLError("hostname did not resolve to any address")
    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise InvalidURLError("hostname resolved to an invalid address") from exc
        if _is_blocked_ip(address):
            raise SSRFBlockedError("hostname resolves to a private, loopback or reserved address")
    return ValidatedTarget(normalized, "https" if port == 443 else "http", host, port, tuple(addresses))


def is_safe_redirect(target_url: str, validated_host: str) -> bool:
    """Cheap synchronous redirect check; full DNS validation happens separately."""

    try:
        _, host, _ = validate_url_syntax(target_url)
    except (InvalidURLError, SSRFBlockedError):
        return False
    return host == validated_host

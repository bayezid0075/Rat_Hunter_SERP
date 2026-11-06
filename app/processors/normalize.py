"""Query, URL and result normalization."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.schemas.common import ContentType
from app.schemas.search import SearchResult

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "gbraid",
    "wbraid",
    "msclkid",
    "yclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "ref_url",
    "_ga",
    "_gl",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_name",
    "utm_reader",
    "utm_social",
    "utm_social-type",
    "spm",
}

_PRODUCT_HINTS = ("/product", "/item", "/p/", "/dp/", "/catalog", "/shop")
_ARTICLE_HINTS = ("/news", "/blog", "/article", "/post", "/story")
_BUSINESS_HINTS = ("/store", "/business", "/listing", "/company")


def normalize_query(query: str) -> str:
    return " ".join(query.split()).strip()


def query_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9\u00c0-\uffff]+", query.casefold()) if len(token) > 1}


def normalize_url(
    url: str,
    *,
    strip_tracking: bool = True,
    sort_query: bool = True,
    remove_fragment: bool = True,
) -> str:
    if not url:
        return url
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return url
    port = ""
    try:
        explicit_port = parts.port
    except ValueError:
        explicit_port = None
    if explicit_port and not (
        (scheme == "http" and explicit_port == 80) or (scheme == "https" and explicit_port == 443)
    ):
        port = f":{explicit_port}"
    netloc = f"{host}{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if strip_tracking:
        lowered = {(key.lower(), value) for key, value in pairs}
        pairs = [(key, value) for key, value in lowered if key not in _TRACKING_PARAMS and not key.startswith("utm_")]
    if sort_query:
        pairs.sort()
    query = urlencode(pairs, doseq=True)
    fragment = "" if remove_fragment else parts.fragment
    return urlunsplit((scheme, netloc, path, query, fragment))


def domain_from_url(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def canonical_url(url: str, canonical: str | None = None) -> str:
    if canonical:
        normalized = normalize_url(canonical)
        if normalized.startswith(("http://", "https://")):
            return normalized
    return normalize_url(url)


def classify_content_type(
    url: str,
    *,
    title: str | None = None,
    snippet: str | None = None,
) -> ContentType:
    haystack = f"{url} {title or ''} {snippet or ''}".casefold()
    if any(hint in haystack for hint in _PRODUCT_HINTS):
        return ContentType.PRODUCT
    if any(hint in haystack for hint in _ARTICLE_HINTS):
        return ContentType.ARTICLE
    if any(hint in haystack for hint in _BUSINESS_HINTS):
        return ContentType.BUSINESS
    return ContentType.WEBPAGE


def domain_allowed(domain: str, include: list[str], exclude: list[str]) -> bool:
    if not domain:
        return not include
    if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in exclude):
        return False
    return not (include and not any(domain == allowed or domain.endswith(f".{allowed}") for allowed in include))


def apply_domain_filters(results: list[SearchResult], include: list[str], exclude: list[str]) -> list[SearchResult]:
    if not include and not exclude:
        return results
    filtered: list[SearchResult] = []
    for result in results:
        domain = result.domain or domain_from_url(result.url)
        if domain_allowed(domain, include, exclude):
            filtered.append(result)
    return filtered

"""Open Graph, Twitter card, HTML metadata and visible-content extraction."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from app.extractors.base import (
    Document,
    ProvenanceCollector,
    absolute_url,
    clean_text,
    soup_from_html,
)
from app.processors.normalize import classify_content_type
from app.schemas.common import ExtractionMethod
from app.schemas.extraction import HeadingData, PageData
from app.utils.datetime import parse_datetime

_TEXT_REMOVE = ("script", "style", "noscript", "svg", "template", "iframe")
_CHROME_REMOVE = ("header", "footer", "nav", "aside", "form")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "your",
    "you",
    "are",
    "was",
    "were",
    "have",
    "has",
    "into",
    "our",
    "their",
    "more",
    "will",
    "can",
    "all",
    "not",
    "but",
    "they",
    "about",
}


@dataclass(slots=True)
class MetadataResult:
    page: PageData
    open_graph: dict[str, str] = field(default_factory=dict)
    twitter: dict[str, str] = field(default_factory=dict)
    meta: dict[str, str] = field(default_factory=dict)


def _meta_value(soup: BeautifulSoup, key: str) -> str | None:
    for attr in ("property", "name", "itemprop"):
        tag = soup.find("meta", attrs={attr: key})
        if isinstance(tag, Tag):
            content = tag.get("content")
            if content:
                return clean_text(str(content))
    return None


def _all_meta(soup: BeautifulSoup, prefix: str, attr: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get(attr)
        content = tag.get("content")
        if isinstance(key, str) and isinstance(content, str) and key.startswith(prefix):
            result[key] = clean_text(content) or content
    return result


def _link(soup: BeautifulSoup, rel: str) -> str | None:
    for tag in soup.find_all("link"):
        raw_values = tag.get("rel")
        values = [raw_values] if isinstance(raw_values, str) else [str(value) for value in (raw_values or [])]
        if any(value.casefold() == rel for value in values):
            href = tag.get("href")
            if href:
                return str(href)
    return None


def _headings(soup: BeautifulSoup) -> list[HeadingData]:
    headings: list[HeadingData] = []
    for level in range(1, 7):
        for tag in soup.find_all(f"h{level}"):
            text = clean_text(tag.get_text(" ", strip=True))
            if text:
                headings.append(HeadingData(level=level, text=text[:500]))
            if len(headings) >= 60:
                return headings
    return headings


def _main_text(document: Document, *, max_chars: int) -> str | None:
    # Parse a private copy: decomposing nodes on the shared document soup would
    # destroy JSON-LD/microdata blocks before the structured extractors run.
    soup = soup_from_html(document.html)
    for tag in soup(list(_TEXT_REMOVE)):
        tag.decompose()
    container: Tag | None = soup.find("main") or soup.find("article") or soup.body
    if container is None:
        return None
    for tag in container.find_all(_CHROME_REMOVE):
        tag.decompose()
    lines = [clean_text(line) for line in container.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    if not text:
        return None
    return text[:max_chars]


def _images(document: Document, soup: BeautifulSoup, og: dict[str, str], tw: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for key in ("og:image", "og:image:url", "og:image:secure_url"):
        if og.get(key):
            candidates.append(og[key])
    for key in ("twitter:image", "twitter:image:src"):
        if tw.get(key):
            candidates.append(tw[key])
    image_src = _link(soup, "image_src")
    if image_src:
        candidates.append(image_src)
    for tag in soup.find_all("img"):
        src = tag.get("src") or tag.get("data-src")
        if src:
            candidates.append(str(src))
        if len(candidates) >= 60:
            break
    resolved: list[str] = []
    for candidate in candidates:
        absolute = absolute_url(document.url, candidate)
        if absolute and absolute not in resolved:
            resolved.append(absolute)
    return resolved[:30]


def _keywords(soup: BeautifulSoup, main_text: str | None, collector: ProvenanceCollector) -> list[str]:
    raw = _meta_value(soup, "keywords")
    if raw:
        keywords = [item.strip() for item in re.split(r"[,;]", raw) if item.strip()]
        if keywords:
            collector.add("page.keywords", keywords, ExtractionMethod.HTML_META, confidence=0.8)
            return keywords[:30]
    if not main_text:
        return []
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9]{3,}", main_text.casefold())
    common = [word for word, _ in Counter(token for token in tokens if token not in _STOPWORDS).most_common(10)]
    if common:
        collector.add("page.keywords", common, ExtractionMethod.HTML, confidence=0.3)
    return common


def extract_metadata(
    document: Document,
    collector: ProvenanceCollector,
    *,
    include_text: bool = True,
    max_text_chars: int = 50_000,
) -> MetadataResult:
    soup = document.soup
    og = _all_meta(soup, "og:", "property")
    tw = _all_meta(soup, "twitter:", "name")
    raw_meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property") or tag.get("itemprop")
        content = tag.get("content")
        if isinstance(name, str) and isinstance(content, str):
            raw_meta[name.casefold()] = content

    html_tag = soup.find("html")
    language = None
    if isinstance(html_tag, Tag) and html_tag.get("lang"):
        language = clean_text(str(html_tag.get("lang")))

    h1 = soup.find("h1")
    title = (
        clean_text(og.get("og:title"))
        or clean_text(tw.get("twitter:title"))
        or clean_text(_meta_value(soup, "title"))
        or (clean_text(soup.title.get_text(" ", strip=True)) if soup.title else None)
        or (clean_text(h1.get_text(" ", strip=True)) if isinstance(h1, Tag) else None)
    )
    if title:
        collector.add("page.title", title, ExtractionMethod.HTML, confidence=0.9)

    description = (
        clean_text(og.get("og:description"))
        or clean_text(tw.get("twitter:description"))
        or clean_text(_meta_value(soup, "description"))
    )
    if description:
        collector.add("page.description", description, ExtractionMethod.HTML_META, confidence=0.8)

    canonical_href = _link(soup, "canonical")
    canonical = absolute_url(document.url, canonical_href) if canonical_href else None
    if canonical:
        collector.add("page.canonical_url", canonical, ExtractionMethod.HTML_META, confidence=0.9)

    headings = _headings(soup)
    main_text = _main_text(document, max_chars=max_text_chars) if include_text else None
    if main_text:
        collector.add("page.main_text", main_text[:200], ExtractionMethod.HTML, confidence=0.7)

    keywords = _keywords(soup, main_text, collector)
    images = _images(document, soup, og, tw)
    favicon_href = _link(soup, "icon") or _link(soup, "shortcut")
    favicon = absolute_url(document.url, favicon_href) if favicon_href else None

    author = _meta_value(soup, "author") or _meta_value(soup, "article:author") or _meta_value(soup, "byl")
    publisher = _meta_value(soup, "publisher") or _meta_value(soup, "og:site_name")
    published = parse_datetime(
        _meta_value(soup, "article:published_time")
        or _meta_value(soup, "datepublished")
        or _meta_value(soup, "date")
        or _meta_value(soup, "pubdate")
    )
    modified = parse_datetime(
        _meta_value(soup, "article:modified_time")
        or _meta_value(soup, "datemodified")
        or _meta_value(soup, "last-modified")
    )

    page = PageData(
        title=title,
        description=description,
        canonical_url=canonical,
        domain=document.url.split("/")[2].lower() if "://" in document.url else None,
        language=language,
        page_type=classify_content_type(document.url, title=title, snippet=description),
        main_text=main_text,
        headings=headings,
        keywords=keywords,
        open_graph=og,
        twitter=tw,
        images=images,
        favicon=favicon,
        published_at=published,
        modified_at=modified,
        author=author,
        publisher=publisher,
    )
    return MetadataResult(page=page, open_graph=og, twitter=tw, meta=raw_meta)


def meta_lookup(result: MetadataResult, *keys: str) -> str | None:
    for key in keys:
        value = result.meta.get(key.casefold())
        if value:
            return clean_text(value)
    return None

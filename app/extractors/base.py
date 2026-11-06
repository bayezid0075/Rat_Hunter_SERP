"""Extraction primitives shared by all parsers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from app.schemas.common import ExtractedField, ExtractionMethod, FieldValidationStatus


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", value).strip()
    return cleaned or None


def absolute_url(base: str, href: str | None) -> str | None:
    if not href:
        return None
    candidate = urljoin(base, href.strip())
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    return candidate.split("#", 1)[0]


def soup_from_html(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


@dataclass(slots=True)
class Document:
    html: str
    url: str
    soup: BeautifulSoup = field(init=False)

    def __post_init__(self) -> None:
        self.soup = soup_from_html(self.html)

    def absolute(self, href: str | None) -> str | None:
        return absolute_url(self.url, href)


class ProvenanceCollector:
    """Collects field-level extraction provenance."""

    def __init__(self, source_url: str) -> None:
        self.source_url = source_url
        self.fields: list[ExtractedField] = []
        self._seen: set[tuple[str, str]] = set()

    def add(
        self,
        field_name: str,
        value: object,
        method: ExtractionMethod | str,
        *,
        confidence: float = 0.6,
        validation: FieldValidationStatus = FieldValidationStatus.UNVERIFIED,
    ) -> object:
        if value is None or value == "" or value == [] or value == {}:
            return value
        marker = (field_name, repr(value))
        if marker in self._seen:
            return value
        self._seen.add(marker)
        self.fields.append(
            ExtractedField(
                field=field_name,
                value=value,
                method=method,
                source_url=self.source_url,
                confidence=max(0.0, min(1.0, confidence)),
                validation_status=validation,
            )
        )
        return value

    def extend(self, fields: list[ExtractedField]) -> None:
        for item in fields:
            marker = (item.field, repr(item.value))
            if marker not in self._seen:
                self._seen.add(marker)
                self.fields.append(item)

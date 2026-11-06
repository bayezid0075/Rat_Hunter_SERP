"""Website-specific extraction plugin contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.extractors.base import Document, ProvenanceCollector
from app.extractors.metadata import MetadataResult
from app.schemas.article import ArticleData
from app.schemas.business import BusinessData
from app.schemas.common import ExtractedField
from app.schemas.product import ProductData


@dataclass(slots=True)
class PluginExtraction:
    product: ProductData | None = None
    article: ArticleData | None = None
    business: BusinessData | None = None
    fields: list[ExtractedField] = field(default_factory=list)


class ExtractionPlugin(ABC):
    name: str
    domains: tuple[str, ...] = ()

    def matches(self, url: str) -> bool:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
        return any(host == domain or host.endswith(f".{domain}") for domain in self.domains)

    @abstractmethod
    def extract(
        self,
        document: Document,
        metadata: MetadataResult,
        collector: ProvenanceCollector,
    ) -> PluginExtraction:
        """Return site-specific structured values."""


class ExtractionPluginRegistry:
    def __init__(self) -> None:
        self._plugins: list[ExtractionPlugin] = []

    def register(self, plugin: ExtractionPlugin) -> None:
        self._plugins.append(plugin)

    def match(self, url: str) -> ExtractionPlugin | None:
        for plugin in self._plugins:
            if plugin.matches(url):
                return plugin
        return None

    @property
    def plugins(self) -> list[ExtractionPlugin]:
        return list(self._plugins)

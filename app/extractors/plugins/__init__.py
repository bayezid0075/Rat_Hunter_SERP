"""Website-specific extraction plugins.

Plugins run after universal structured metadata and HTML heuristics.  They are
matched by domain and can never affect unrelated sites.
"""

from app.extractors.plugins.base import (
    ExtractionPlugin,
    ExtractionPluginRegistry,
    PluginExtraction,
)
from app.extractors.plugins.daraz import DarazExtractionPlugin


def build_extraction_plugins() -> ExtractionPluginRegistry:
    registry = ExtractionPluginRegistry()
    registry.register(DarazExtractionPlugin())
    return registry


__all__ = [
    "DarazExtractionPlugin",
    "ExtractionPlugin",
    "ExtractionPluginRegistry",
    "PluginExtraction",
    "build_extraction_plugins",
]

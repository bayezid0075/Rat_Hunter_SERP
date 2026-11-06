"""Optional AI fallback interface.

The core project deliberately ships no mandatory commercial AI integration.
Operators may supply an implementation of this interface; when absent the
pipeline simply relies on deterministic extraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIExtractor(ABC):
    enabled: bool = False

    @abstractmethod
    async def extract_structured(self, html: str, url: str, *, wanted: list[str]) -> dict[str, Any]:
        """Return partial structured values. Values must be grounded in the HTML."""


class NoopAIExtractor(AIExtractor):
    enabled = False

    async def extract_structured(self, html: str, url: str, *, wanted: list[str]) -> dict[str, Any]:
        return {}

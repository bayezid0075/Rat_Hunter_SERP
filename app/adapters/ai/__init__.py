"""Optional AI extraction adapters (never required for basic extraction)."""

from app.adapters.ai.base import AIExtractor, NoopAIExtractor

__all__ = ["AIExtractor", "NoopAIExtractor"]

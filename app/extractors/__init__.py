"""Universal two-stage web extraction engine."""

from app.extractors.browser import BrowserPool
from app.extractors.http import FetchResult, HttpFetcher
from app.extractors.pipeline import ExtractionPipeline

__all__ = ["BrowserPool", "ExtractionPipeline", "FetchResult", "HttpFetcher"]

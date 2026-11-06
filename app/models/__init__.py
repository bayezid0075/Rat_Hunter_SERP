"""SQLAlchemy ORM models.

Importing this package registers every table on ``Base.metadata`` so Alembic
and test bootstrap code see the complete schema.
"""

from app.models.account import Account
from app.models.api_key import ApiKey
from app.models.application import Application
from app.models.extraction_job import ExtractionJob
from app.models.indexed_document import IndexedDocument
from app.models.search_history import SearchHistory
from app.models.search_source import SearchSource
from app.models.source_health import SourceHealth
from app.models.system_setting import SystemSetting
from app.models.usage_log import UsageLog

__all__ = [
    "Account",
    "ApiKey",
    "Application",
    "ExtractionJob",
    "IndexedDocument",
    "SearchHistory",
    "SearchSource",
    "SourceHealth",
    "SystemSetting",
    "UsageLog",
]

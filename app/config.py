"""Application configuration.

All operational limits are environment driven.  Nothing in this module performs
I/O, which keeps configuration importable by API processes, workers and tests.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _as_list(value: Any, *, cast: type = str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("["):
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                raise ValueError("expected a JSON list")
            return [cast(item) for item in parsed]
        return [cast(part.strip()) for part in raw.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [cast(item) for item in value]
    raise ValueError("expected a comma separated string or a list")


class Settings(BaseSettings):
    """Typed application settings loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    # Application ---------------------------------------------------------
    app_name: str = "Universal SERP API"
    app_version: str = "1.0.0"
    environment: str = "development"
    debug: bool = False
    api_v1_prefix: str = "/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    log_level: str = "INFO"
    log_json: bool = True
    request_id_header: str = "X-Request-ID"
    max_request_body_bytes: int = 1_000_000
    cors_origins: list[str] = Field(default_factory=list)
    allowed_hosts: list[str] = Field(default_factory=lambda: ["*"])

    # Secrets -------------------------------------------------------------
    secret_key: str = "change-me-in-development"
    api_key_pepper: str = "change-me-in-development-too"

    # Database ------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://serp:serp@localhost:5432/serp"
    database_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0

    # Redis / cache -------------------------------------------------------
    redis_url: str = "redis://localhost:6379/0"
    redis_max_connections: int = 50
    cache_backend: Literal["redis", "memory"] = "redis"
    enable_cache: bool = True
    serp_cache_ttl: int = 45
    serp_cache_stale_ttl: int = 90
    product_serp_cache_ttl: int = 20
    product_serp_stale_ttl: int = 45
    extraction_cache_ttl: int = 180
    extraction_stale_ttl: int = 600
    website_metadata_ttl: int = 3600
    source_health_ttl: int = 60
    cache_lock_timeout: int = 10

    # API access ----------------------------------------------------------
    default_rate_limit_per_minute: int = 120
    default_daily_quota: int = 10_000
    api_key_cache_ttl: int = 30

    # Concurrency ---------------------------------------------------------
    global_search_concurrency: int = 20
    http_extraction_concurrency: int = 20
    per_adapter_concurrency: int = 5
    per_domain_concurrency: int = 4
    browser_instances: int = 2
    browser_pages_per_instance: int = 4
    distributed_concurrency_ttl: int = 60

    # Deadlines / resilience ---------------------------------------------
    quick_search_timeout: float = 8.0
    deep_search_timeout: float = 15.0
    adapter_timeout: float = 6.0
    extraction_timeout: float = 8.0
    global_request_deadline: float = 20.0
    search_retries: int = 1
    circuit_breaker_threshold: int = 5
    circuit_breaker_cooldown: int = 30

    # Outbound HTTP -------------------------------------------------------
    http_timeout: float = 10.0
    http_connect_timeout: float = 5.0
    http_max_response_bytes: int = 5_000_000
    http_max_redirects: int = 5
    http_user_agent: str = "UniversalSerpBot/1.0 (+https://example.local/bot)"
    http_allowed_ports: list[int] = Field(default_factory=lambda: [80, 443])
    ssrf_allow_private: bool = False
    ssrf_dns_timeout: float = 3.0

    # Google adapter ------------------------------------------------------
    google_enabled: bool = True
    google_base_url: str = "https://www.google.com"
    google_headless: bool = True
    google_locale: str = "en-US"
    google_region: str = "US"
    google_timeout_ms: int = 15_000
    google_navigation_timeout_ms: int = 15_000
    google_results_per_page: int = 10
    google_max_pages: int = 2
    google_selectors_json: str = ""

    # Direct website adapters --------------------------------------------
    direct_websites_enabled: bool = False
    direct_website_timeout: float = 8.0
    direct_daraz_enabled: bool = True
    direct_pickaboo_enabled: bool = True
    direct_startech_enabled: bool = True
    direct_gadgetandgear_enabled: bool = True

    # Internal index ------------------------------------------------------
    custom_index_enabled: bool = True

    # Ranking -------------------------------------------------------------
    ranking_weights_json: str = ""

    # Extraction ----------------------------------------------------------
    deep_search_extract_limit: int = 5
    deep_search_extract_concurrency: int = 5
    browser_fallback: bool = True
    extraction_max_text_chars: int = 50_000

    # Observability -------------------------------------------------------
    metrics_enabled: bool = True
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    # Workers -------------------------------------------------------------
    worker_search_queue: str = "serp:search"
    worker_extraction_queue: str = "serp:extraction"
    worker_browser_queue: str = "serp:browser"
    job_max_retries: int = 2
    job_timeout: int = 120

    @field_validator("api_v1_prefix", mode="before")
    @classmethod
    def _normalize_api_prefix(cls, value: Any) -> str:
        if not isinstance(value, str):
            return value
        prefix = value.strip()
        if not prefix:
            return "/v1"
        # Git Bash / MSYS rewrites POSIX-looking environment values (e.g. "/v1")
        # into Windows paths ("C:/Program Files/Git/v1") when launching native
        # binaries.  Recover the original prefix if that happened.
        if len(prefix) > 2 and prefix[1] == ":" and prefix[2] in ("/", "\\"):
            marker = prefix.lower().find("/git/")
            if marker != -1:
                prefix = prefix[marker + 4 :]
            else:
                prefix = "/" + prefix[2:].lstrip("/\\").split("/")[-1]
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        return prefix.rstrip("/") or "/"

    @field_validator("cors_origins", "allowed_hosts", mode="before")
    @classmethod
    def _validate_str_list(cls, value: Any) -> list[str]:
        return _as_list(value, cast=str)

    @field_validator("http_allowed_ports", mode="before")
    @classmethod
    def _validate_port_list(cls, value: Any) -> list[int]:
        ports = _as_list(value, cast=int)
        for port in ports:
            if not 1 <= port <= 65_535:
                raise ValueError(f"invalid port number: {port}")
        return ports

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def google_selectors(self) -> dict[str, Any]:
        if not self.google_selectors_json:
            return {}
        parsed = json.loads(self.google_selectors_json)
        if not isinstance(parsed, dict):
            raise ValueError("GOOGLE_SELECTORS_JSON must be a JSON object")
        return parsed

    @property
    def ranking_weights(self) -> dict[str, float]:
        if not self.ranking_weights_json:
            return {}
        parsed = json.loads(self.ranking_weights_json)
        if not isinstance(parsed, dict):
            raise ValueError("RANKING_WEIGHTS_JSON must be a JSON object")
        return {str(key): float(value) for key, value in parsed.items()}

    def cache_ttls(self, product: bool = False) -> tuple[int, int]:
        if product:
            return self.product_serp_cache_ttl, self.product_serp_stale_ttl
        return self.serp_cache_ttl, self.serp_cache_stale_ttl


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""

    return Settings()

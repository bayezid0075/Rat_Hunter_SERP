"""Optional Sentry integration; the application starts without it."""

from __future__ import annotations

from app.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def init_sentry(settings: Settings) -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=max(0.0, min(1.0, settings.sentry_traces_sample_rate)),
            send_default_pii=False,
        )
        logger.info("sentry_initialized")
    except Exception:
        logger.warning("sentry_initialization_failed", exc_info=True)

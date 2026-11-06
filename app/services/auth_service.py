"""API key authentication, per-key rate limits and daily quotas."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.core.cache import CacheManager
from app.core.exceptions import (
    AuthenticationError,
    DatabaseUnavailableError,
    ForbiddenError,
    QuotaExceededError,
    RateLimitError,
)
from app.core.logging import get_logger
from app.core.rate_limit import RateLimiter
from app.core.security import hash_api_key
from app.models.api_key import ApiKey
from app.models.usage_log import UsageLog

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


@dataclass(slots=True)
class AuthContext:
    api_key_id: UUID
    account_id: UUID
    application_id: UUID | None
    key_name: str
    key_prefix: str
    account_name: str
    account_email: str | None
    plan: str
    rate_limit_per_minute: int
    daily_quota: int
    expires_at: datetime | None = None


class AuthService:
    def __init__(
        self,
        *,
        settings: Settings,
        cache: CacheManager,
        rate_limiter: RateLimiter,
    ) -> None:
        self.settings = settings
        self.cache = cache
        self.rate_limiter = rate_limiter

    async def authenticate(self, raw_key: str, session: AsyncSession) -> AuthContext:
        if not raw_key or len(raw_key) < 16:
            raise AuthenticationError("A valid API key is required")
        key_hash = hash_api_key(raw_key, self.settings.api_key_pepper)
        cache_key = f"serp:auth:{key_hash}"

        async def load() -> dict[str, Any]:
            stmt = (
                select(ApiKey)
                .where(ApiKey.key_hash == key_hash)
                .options(selectinload(ApiKey.account), selectinload(ApiKey.application))
            )
            try:
                result = await session.execute(stmt)
                api_key = result.scalar_one_or_none()
            except SQLAlchemyError as exc:
                raise DatabaseUnavailableError("authentication store is unavailable") from exc
            if api_key is None:
                raise AuthenticationError()
            account = api_key.account
            application = api_key.application
            return {
                "api_key_id": str(api_key.id),
                "account_id": str(api_key.account_id),
                "application_id": str(api_key.application_id) if api_key.application_id else None,
                "key_name": api_key.name,
                "key_prefix": api_key.key_prefix,
                "is_active": api_key.is_active,
                "revoked_at": api_key.revoked_at.isoformat() if api_key.revoked_at else None,
                "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
                "rate_limit_per_minute": api_key.rate_limit_per_minute,
                "daily_quota": api_key.daily_quota,
                "account_name": account.name,
                "account_email": account.email,
                "account_active": account.is_active,
                "plan": account.plan,
                "account_rate_limit": account.rate_limit_per_minute,
                "account_daily_quota": account.daily_quota,
                "application_active": application.is_active if application else True,
            }

        entry = await self.cache.get_or_set(
            cache_key,
            load,
            fresh_ttl=self.settings.api_key_cache_ttl,
            stale_ttl=self.settings.api_key_cache_ttl * 2,
        )
        snapshot: dict[str, Any] = entry.value
        self._validate_snapshot(snapshot)

        rate_limit = (
            snapshot.get("rate_limit_per_minute")
            or snapshot.get("account_rate_limit")
            or self.settings.default_rate_limit_per_minute
        )
        rate = await self.rate_limiter.check(f"key:{snapshot['api_key_id']}", int(rate_limit))
        if not rate.allowed:
            raise RateLimitError(
                "Rate limit exceeded",
                details={
                    "limit": rate.limit,
                    "used": rate.used,
                    "remaining": rate.remaining,
                    "reset_after_seconds": round(rate.reset_after_seconds, 1),
                },
            )
        quota = snapshot.get("daily_quota") or snapshot.get("account_daily_quota") or self.settings.default_daily_quota
        account_id = UUID(snapshot["account_id"])
        used_today = await self._used_today(session, account_id)
        if used_today >= int(quota):
            raise QuotaExceededError(
                "Daily API quota exceeded",
                details={"quota": int(quota), "used": used_today},
            )
        await self._touch_key(session, UUID(snapshot["api_key_id"]))
        context = AuthContext(
            api_key_id=UUID(snapshot["api_key_id"]),
            account_id=account_id,
            application_id=UUID(snapshot["application_id"]) if snapshot.get("application_id") else None,
            key_name=str(snapshot["key_name"]),
            key_prefix=str(snapshot["key_prefix"]),
            account_name=str(snapshot["account_name"]),
            account_email=snapshot.get("account_email"),
            plan=str(snapshot["plan"]),
            rate_limit_per_minute=int(rate_limit),
            daily_quota=int(quota),
            expires_at=datetime.fromisoformat(snapshot["expires_at"]) if snapshot.get("expires_at") else None,
        )
        return context

    @staticmethod
    def _validate_snapshot(snapshot: dict[str, Any]) -> None:
        if not snapshot.get("is_active") or snapshot.get("revoked_at"):
            raise ForbiddenError("This API key has been revoked")
        if not snapshot.get("account_active", True):
            raise ForbiddenError("This account is disabled")
        if not snapshot.get("application_active", True):
            raise ForbiddenError("This application is disabled")
        expires_at = snapshot.get("expires_at")
        if expires_at:
            try:
                expiry = datetime.fromisoformat(expires_at)
            except ValueError:
                raise AuthenticationError("API key expiry is invalid") from None
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=UTC)
            if expiry <= datetime.now(UTC):
                raise AuthenticationError("This API key has expired")

    async def _used_today(self, session: AsyncSession, account_id: UUID) -> int:
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            result = await session.execute(
                select(func.count(UsageLog.id)).where(
                    UsageLog.account_id == account_id,
                    UsageLog.created_at >= day_start,
                )
            )
            return int(result.scalar_one() or 0)
        except SQLAlchemyError:
            logger.warning("quota_check_failed", exc_info=True)
            return 0

    async def _touch_key(self, session: AsyncSession, api_key_id: UUID) -> None:
        try:
            result = await session.execute(select(ApiKey).where(ApiKey.id == api_key_id))
            api_key = result.scalar_one_or_none()
            if api_key is None:
                return
            now = datetime.now(UTC)
            last_used = api_key.last_used_at
            if last_used is not None and last_used.tzinfo is None:
                last_used = last_used.replace(tzinfo=UTC)
            if last_used is None or (now - last_used).total_seconds() >= 60:
                api_key.last_used_at = now
                await session.commit()
        except SQLAlchemyError:
            await session.rollback()
            logger.warning("api_key_touch_failed", exc_info=True)


def extract_bearer_key(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def now_ms() -> int:
    return int(time.time() * 1000)

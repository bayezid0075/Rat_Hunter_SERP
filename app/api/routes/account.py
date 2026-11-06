"""Account and usage endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import CurrentAuth, DbSession, get_usage_service
from app.models.account import Account
from app.models.api_key import ApiKey
from app.models.application import Application
from app.schemas.account import AccountInfo, ApiKeyInfo, UsageResponse
from app.services.usage_service import UsageService

router = APIRouter(tags=["account"])


@router.get("/account", response_model=AccountInfo, summary="Current API account")
async def get_account(
    auth: CurrentAuth,
    session: DbSession,
) -> AccountInfo:
    api_key = await session.get(ApiKey, auth.api_key_id)
    account = await session.get(Account, auth.account_id)
    application = await session.get(Application, auth.application_id) if auth.application_id is not None else None
    if api_key is None or account is None:
        # Authentication already validated these rows; treat disappearance as not found.
        from app.core.exceptions import NotFoundError

        raise NotFoundError("account not found")
    application_info = (
        {
            "id": str(application.id),
            "name": application.name,
            "description": application.description,
            "is_active": application.is_active,
        }
        if application is not None
        else None
    )
    return AccountInfo(
        id=account.id,
        name=account.name,
        email=account.email,
        plan=account.plan,
        is_active=account.is_active,
        application=application_info,
        api_key=ApiKeyInfo(
            id=api_key.id,
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            is_active=api_key.is_active,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            expires_at=api_key.expires_at,
            rate_limit_per_minute=api_key.rate_limit_per_minute,
            daily_quota=api_key.daily_quota,
        ),
    )


@router.get(
    "/account/usage",
    response_model=UsageResponse,
    summary="API usage statistics",
)
async def get_usage(
    auth: CurrentAuth,
    usage: UsageService = Depends(get_usage_service),
    days: int = Query(default=30, ge=1, le=365),
) -> UsageResponse:
    stats, period_start, period_end = await usage.stats(auth.account_id, days=days)
    today = await usage.today_count(auth.account_id)
    stats.quota = auth.daily_quota
    stats.quota_remaining = max(0, auth.daily_quota - today)
    stats.rate_limit_per_minute = auth.rate_limit_per_minute
    return UsageResponse(
        account_id=auth.account_id,
        period_start=period_start,
        period_end=period_end,
        stats=stats,
    )

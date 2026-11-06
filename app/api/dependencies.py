"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.search.registry import AdapterRegistry
from app.core.cache import CacheManager
from app.core.exceptions import AuthenticationError
from app.core.logging import set_account_id
from app.core.orchestrator import SearchOrchestrator
from app.services.auth_service import AuthContext, AuthService, extract_bearer_key
from app.services.extraction_service import ExtractionService
from app.services.job_service import JobService
from app.services.usage_service import UsageService


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.db.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_current_auth(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthContext:
    raw_key = x_api_key or extract_bearer_key(authorization)
    if not raw_key:
        raise AuthenticationError("Provide an X-API-Key header or Bearer token")
    auth_service: AuthService = request.app.state.auth_service
    context = await auth_service.authenticate(raw_key, session)
    request.state.auth = context
    set_account_id(str(context.account_id))
    return context


def get_orchestrator(request: Request) -> SearchOrchestrator:
    return cast(SearchOrchestrator, request.app.state.orchestrator)


def get_extraction_service(request: Request) -> ExtractionService:
    return cast(ExtractionService, request.app.state.extraction_service)


def get_job_service(request: Request) -> JobService:
    return cast(JobService, request.app.state.job_service)


def get_usage_service(request: Request) -> UsageService:
    return cast(UsageService, request.app.state.usage_service)


def get_registry(request: Request) -> AdapterRegistry:
    return cast(AdapterRegistry, request.app.state.registry)


def get_cache(request: Request) -> CacheManager:
    return cast(CacheManager, request.app.state.cache)


CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]
DbSession = Annotated[AsyncSession, Depends(get_db_session)]

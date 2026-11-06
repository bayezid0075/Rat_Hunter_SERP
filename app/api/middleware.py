"""Request correlation, access logging, security headers and body limits."""

from __future__ import annotations

import re
import time
import uuid
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.exceptions import ErrorCode, error_payload
from app.core.logging import (
    get_logger,
    set_account_id,
    set_request_id,
)
from app.monitoring.metrics import observe_request

logger = get_logger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = request.app.state.settings
        incoming = request.headers.get(settings.request_id_header)
        request_id = incoming if incoming and _REQUEST_ID_RE.match(incoming) else uuid.uuid4().hex
        token = set_request_id(request_id)
        request.state.request_id = request_id
        request.state.cached = False
        try:
            response = await call_next(request)
            response.headers[settings.request_id_header] = request_id
            return response
        finally:
            set_account_id(None)
            set_request_id(None)
            token.var.reset(token)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = request.app.state.settings
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
            except ValueError:
                length = 0
            if length > settings.max_request_body_bytes:
                return JSONResponse(
                    status_code=413,
                    content=error_payload(
                        ErrorCode.PAYLOAD_TOO_LARGE,
                        "Request body exceeds the configured limit",
                        request_id=getattr(request.state, "request_id", None),
                    ),
                )
        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response: Response | None = None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            logger.exception(
                "request_failed",
                extra={"method": request.method, "path": request.url.path},
            )
            raise
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            endpoint = getattr(route, "path", request.url.path)
            observe_request(endpoint, request.method, status_code, elapsed)
            auth = getattr(request.state, "auth", None)
            if auth is not None:
                usage = getattr(request.app.state, "usage_service", None)
                if usage is not None:
                    try:
                        await usage.record(
                            account_id=auth.account_id,
                            api_key_id=auth.api_key_id,
                            endpoint=endpoint,
                            request_id=getattr(request.state, "request_id", None),
                            status_code=status_code,
                            processing_time_ms=round(elapsed * 1000, 2),
                            cached=bool(getattr(request.state, "cached", False)),
                        )
                    except Exception:
                        logger.warning("usage_record_failed", exc_info=True)
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": round(elapsed * 1000, 2),
                },
            )
        assert response is not None
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        return response


def security_middleware_parameters(settings: Any) -> dict[str, Any]:  # pragma: no cover
    return {"allowed_hosts": settings.allowed_hosts}

"""Centralized exception hierarchy and error classification."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, get_request_id


class ErrorCode(StrEnum):
    INVALID_QUERY = "invalid_query"
    INVALID_URL = "invalid_url"
    SSRF_BLOCKED = "ssrf_blocked"
    AUTHENTICATION_FAILED = "authentication_failed"
    FORBIDDEN = "forbidden"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    QUOTA_EXCEEDED = "quota_exceeded"
    SEARCH_SOURCE_UNAVAILABLE = "search_source_unavailable"
    SEARCH_TIMEOUT = "search_timeout"
    CAPTCHA_DETECTED = "captcha_detected"
    ACCESS_DENIED = "access_denied"
    EXTRACTION_FAILED = "extraction_failed"
    INVALID_STRUCTURED_DATA = "invalid_structured_data"
    DATABASE_UNAVAILABLE = "database_unavailable"
    REDIS_UNAVAILABLE = "redis_unavailable"
    WORKER_UNAVAILABLE = "worker_unavailable"
    NOT_FOUND = "not_found"
    RATE_LIMITED = "rate_limited"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    SERVICE_UNAVAILABLE = "service_unavailable"


class AppError(Exception):
    """Base class for expected, client-safe application errors."""

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    message: str = "Internal server error"
    retryable: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any = None,
        code: ErrorCode | None = None,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        super().__init__(self.message)


class InvalidQueryError(AppError):
    status_code = 422
    code = ErrorCode.INVALID_QUERY
    message = "The search query is invalid"


class InvalidURLError(AppError):
    status_code = 422
    code = ErrorCode.INVALID_URL
    message = "The supplied URL is invalid"


class SSRFBlockedError(AppError):
    status_code = 400
    code = ErrorCode.SSRF_BLOCKED
    message = "The supplied URL is not permitted"


class AuthenticationError(AppError):
    status_code = 401
    code = ErrorCode.AUTHENTICATION_FAILED
    message = "Invalid or missing API key"


class ForbiddenError(AppError):
    status_code = 403
    code = ErrorCode.FORBIDDEN
    message = "Access to this resource is forbidden"


class RateLimitError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMIT_EXCEEDED
    message = "Rate limit exceeded"
    retryable = True


class QuotaExceededError(AppError):
    status_code = 429
    code = ErrorCode.QUOTA_EXCEEDED
    message = "API quota exceeded"


class SourceUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.SEARCH_SOURCE_UNAVAILABLE
    message = "The search source is currently unavailable"
    retryable = True


class SourceTimeoutError(AppError):
    status_code = 504
    code = ErrorCode.SEARCH_TIMEOUT
    message = "The search source timed out"
    retryable = True


class CaptchaDetectedError(AppError):
    status_code = 503
    code = ErrorCode.CAPTCHA_DETECTED
    message = "The upstream source presented a CAPTCHA challenge"
    retryable = False


class AccessDeniedError(AppError):
    status_code = 403
    code = ErrorCode.ACCESS_DENIED
    message = "The upstream source denied access"
    retryable = False


class ExtractionError(AppError):
    status_code = 502
    code = ErrorCode.EXTRACTION_FAILED
    message = "Extraction failed"
    retryable = True


class InvalidStructuredDataError(AppError):
    status_code = 422
    code = ErrorCode.INVALID_STRUCTURED_DATA
    message = "Structured data could not be parsed"


class DatabaseUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.DATABASE_UNAVAILABLE
    message = "The database is unavailable"
    retryable = True


class RedisUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.REDIS_UNAVAILABLE
    message = "The cache service is unavailable"
    retryable = True


class WorkerUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.WORKER_UNAVAILABLE
    message = "The background worker is unavailable"
    retryable = True


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND
    message = "The requested resource was not found"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = ErrorCode.PAYLOAD_TOO_LARGE
    message = "The request payload is too large"


class InternalError(AppError):
    status_code = 500
    code = ErrorCode.INTERNAL_ERROR
    message = "An internal error occurred"


class BrowserUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    message = "The browser worker is unavailable"
    retryable = True


class ConcurrencyTimeoutError(AppError):
    status_code = 503
    code = ErrorCode.SERVICE_UNAVAILABLE
    message = "The service is at capacity; retry shortly"
    retryable = True


def error_payload(
    code: ErrorCode | str,
    message: str,
    *,
    request_id: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": False,
        "error": {
            "code": str(code),
            "message": message,
        },
        "request_id": request_id or get_request_id(),
    }
    if details is not None:
        payload["error"]["details"] = details
    return payload


logger = get_logger(__name__)


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    if exc.status_code >= 500:
        logger.error(
            "application_error",
            extra={"error_code": str(exc.code), "status_code": exc.status_code},
            exc_info=exc,
        )
    else:
        logger.info(
            "client_error",
            extra={"error_code": str(exc.code), "status_code": exc.status_code},
        )
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc.code, exc.message, details=exc.details),
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = []
    for error in exc.errors():
        details.append(
            {
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
        )
    return JSONResponse(
        status_code=422,
        content=error_payload(ErrorCode.VALIDATION_ERROR, "Request validation failed", details=details),
    )


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = {
        401: ErrorCode.AUTHENTICATION_FAILED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        413: ErrorCode.PAYLOAD_TOO_LARGE,
        429: ErrorCode.RATE_LIMITED,
        503: ErrorCode.SERVICE_UNAVAILABLE,
    }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(code, message),
        headers=getattr(exc, "headers", None),
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception")
    return JSONResponse(
        status_code=500,
        content=error_payload(ErrorCode.INTERNAL_ERROR, "An internal error occurred"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

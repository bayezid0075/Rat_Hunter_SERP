"""Structured JSON logging with request correlation."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
account_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar("account_id", default=None)


def get_request_id() -> str | None:
    return request_id_ctx.get()


def set_request_id(request_id: str | None) -> contextvars.Token[str | None]:
    return request_id_ctx.set(request_id)


def set_account_id(account_id: str | None) -> contextvars.Token[str | None]:
    return account_id_ctx.set(account_id)


class JsonFormatter(logging.Formatter):
    """Minimal dependency-free JSON log formatter."""

    _reserved = frozenset(logging.makeLogRecord({}).__dict__) | {
        "asctime",
        "message",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        account_id = account_id_ctx.get()
        if account_id:
            payload["account_id"] = account_id
        for key, value in record.__dict__.items():
            if key not in self._reserved and not key.startswith("_"):
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    value = repr(value)
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s [request_id=%(request_id)s] %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return super().format(record)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure the root logger exactly once per process."""

    root = logging.getLogger()
    root.setLevel(level.upper())
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())
    root.addHandler(handler)

    for noisy in ("uvicorn.access", "watchfiles", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(max(logging.WARNING, root.level))

    logging.captureWarnings(True)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

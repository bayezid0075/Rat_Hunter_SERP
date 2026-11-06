"""ARQ search worker entry point.

Run with:
    arq app.workers.search_worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings

from app.config import get_settings
from app.workers.runtime import build_runtime
from app.workers.tasks import process_search_job

_settings = get_settings()


async def startup(ctx: dict[Any, Any]) -> None:
    ctx["runtime"] = await build_runtime(_settings)


async def shutdown(ctx: dict[Any, Any]) -> None:
    runtime = ctx.get("runtime")
    if runtime is not None:
        await runtime.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_search_job]
    queue_name = _settings.worker_search_queue
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = max(1, min(50, _settings.global_search_concurrency))
    job_timeout = _settings.job_timeout
    max_tries = _settings.job_max_retries + 1
    keep_result = 3600
    health_check_key = "serp:worker:search:health"

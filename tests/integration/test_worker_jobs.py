"""Background task execution tests without a live ARQ/Redis worker process."""

from __future__ import annotations

from typing import Any

import pytest
from app.schemas.extraction import ExtractionPayload, ExtractionResponse, PageData
from app.schemas.search import SearchRequest
from app.workers.runtime import WorkerRuntime
from app.workers.tasks import process_browser_job, process_extraction_job, process_search_job
from tests.support import Harness


def _runtime(harness: Harness, *, extraction: Any | None = None) -> WorkerRuntime:
    state = harness.app.state
    return WorkerRuntime(
        settings=harness.settings,
        db=harness.db,
        cache=harness.cache,
        concurrency=state.concurrency,
        http=harness.http,
        browser=state.browser_pool,
        registry=state.registry,
        pipeline=state.pipeline,
        extraction=extraction or state.extraction_service,
        orchestrator=state.orchestrator,
        jobs=state.job_service,
    )


@pytest.mark.asyncio
async def test_search_job_executes_and_persists_result(harness: Harness) -> None:
    jobs = harness.app.state.job_service
    job = await jobs.create_job(
        account_id=harness.account_id,
        api_key_id=harness.api_key_id,
        job_type="search",
        payload={
            "request": SearchRequest(query="laptop").model_dump(mode="json"),
            "account_id": str(harness.account_id),
            "request_id": "worker-search-1",
        },
        request_id="worker-search-1",
        function_name="process_search_job",
        queue_name=harness.settings.worker_search_queue,
    )

    outcome = await process_search_job({"runtime": _runtime(harness), "job_try": 1}, str(job.id))
    assert outcome == {"job_id": str(job.id), "status": "succeeded"}

    stored = await jobs.load(job.id)
    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.attempts == 1
    assert stored.result is not None
    assert stored.result["metadata"]["query"] == "laptop"
    assert stored.result["results"]


@pytest.mark.asyncio
async def test_extraction_job_executes_and_persists_result(harness: Harness) -> None:
    jobs = harness.app.state.job_service
    job = await jobs.create_job(
        account_id=harness.account_id,
        api_key_id=harness.api_key_id,
        job_type="extraction",
        payload={
            "urls": ["https://example.com/products/acme-ultrabook-14"],
            "include_text": True,
            "include_media": True,
            "allow_browser": False,
            "request_id": "worker-extract-1",
        },
        request_id="worker-extract-1",
        function_name="process_extraction_job",
        queue_name=harness.settings.worker_extraction_queue,
    )

    outcome = await process_extraction_job({"runtime": _runtime(harness), "job_try": 1}, str(job.id))
    assert outcome == {"job_id": str(job.id), "status": "succeeded"}

    stored = await jobs.load(job.id)
    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.result is not None
    assert stored.result["data"]["product"]["name"] == "Acme UltraBook 14"


@pytest.mark.asyncio
async def test_browser_job_forces_browser_permission(harness: Harness) -> None:
    class RecordingExtraction:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def extract_url(self, url: str, **kwargs: Any) -> ExtractionResponse:
            self.calls.append({"url": url, **kwargs})
            return ExtractionResponse(
                requested_url=url,
                data=ExtractionPayload(
                    requested_url=url,
                    final_url=url,
                    page=PageData(title="browser"),
                    browser_used=True,
                ),
            )

    recording = RecordingExtraction()
    jobs = harness.app.state.job_service
    job = await jobs.create_job(
        account_id=harness.account_id,
        api_key_id=harness.api_key_id,
        job_type="extraction",
        payload={
            "urls": ["https://example.com/browser"],
            "allow_browser": False,
            "request_id": "worker-browser-1",
        },
        request_id="worker-browser-1",
        function_name="process_browser_job",
        queue_name=harness.settings.worker_browser_queue,
    )

    outcome = await process_browser_job({"runtime": _runtime(harness, extraction=recording), "job_try": 1}, str(job.id))
    assert outcome["status"] == "succeeded"
    assert recording.calls[0]["allow_browser"] is True

    stored = await jobs.load(job.id)
    assert stored is not None
    assert stored.result is not None
    assert stored.result["data"]["browser_used"] is True


@pytest.mark.asyncio
async def test_job_failure_is_persisted(harness: Harness) -> None:
    jobs = harness.app.state.job_service
    job = await jobs.create_job(
        account_id=harness.account_id,
        api_key_id=harness.api_key_id,
        job_type="extraction",
        payload={"urls": [], "request_id": "worker-failure-1"},
        request_id="worker-failure-1",
        function_name="process_extraction_job",
        queue_name=harness.settings.worker_extraction_queue,
    )

    outcome = await process_extraction_job({"runtime": _runtime(harness), "job_try": 1}, str(job.id))
    assert outcome["status"] == "failed"

    stored = await jobs.load(job.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error == "job payload contains no URLs"
    assert stored.finished_at is not None

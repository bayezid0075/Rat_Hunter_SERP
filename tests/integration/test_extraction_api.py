from __future__ import annotations

from tests.support import PRODUCT_HTML, Harness


async def test_extract_single_url_and_cache(harness: Harness) -> None:
    harness.http.html = PRODUCT_HTML
    payload = {"url": "https://example.com/products/acme-ultrabook-14"}
    first = await harness.client.post("/v1/extract", json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["success"] is True
    assert body["cached"] is False
    assert body["data"]["product"]["name"] == "Acme UltraBook 14"
    assert body["data"]["product"]["price"]["amount"] == 1299.0
    assert body["data"]["page"]["title"] == "Acme UltraBook 14"

    second = await harness.client.post("/v1/extract", json=payload)
    assert second.status_code == 200
    assert second.json()["cached"] is True
    assert len(harness.http.calls) == 1


async def test_extract_blocks_private_targets(harness: Harness) -> None:
    response = await harness.client.post("/v1/extract", json={"url": "http://127.0.0.1/latest"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ssrf_blocked"


async def test_extract_rejects_invalid_scheme(harness: Harness) -> None:
    response = await harness.client.post("/v1/extract", json={"url": "file:///etc/passwd"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_extract_batch(harness: Harness) -> None:
    harness.http.html = PRODUCT_HTML
    response = await harness.client.post(
        "/v1/extract/batch",
        json={
            "urls": [
                "https://example.com/products/acme-ultrabook-14",
                "https://example.com/products/another",
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert len(body["results"]) == 2
    assert body["errors"] == []


async def test_extract_batch_reports_partial_errors(harness: Harness) -> None:
    harness.http.html = PRODUCT_HTML
    response = await harness.client.post(
        "/v1/extract/batch",
        json={
            "urls": [
                "https://example.com/products/acme-ultrabook-14",
                "http://127.0.0.1/private",
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert len(body["results"]) == 1
    assert body["errors"][0]["code"] == "ssrf_blocked"


async def test_async_extract_queues_job(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/extract",
        json={"url": "https://example.com/a", "async": True, "allow_browser": False},
    )
    assert response.status_code == 202
    body = response.json()
    assert harness.arq.enqueued[0]["function"] == "process_extraction_job"
    assert harness.arq.enqueued[0]["kwargs"]["_queue_name"] == "serp:extraction"

    status = await harness.client.get(body["status_url"])
    assert status.status_code == 200
    assert status.json()["job_type"] == "extraction"


async def test_async_extract_browser_queue(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/extract",
        json={"url": "https://example.com/a", "async": True, "allow_browser": True},
    )
    assert response.status_code == 202
    assert harness.arq.enqueued[0]["function"] == "process_browser_job"
    assert harness.arq.enqueued[0]["kwargs"]["_queue_name"] == "serp:browser"

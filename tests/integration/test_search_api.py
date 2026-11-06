from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.exceptions import SourceTimeoutError
from app.schemas.product import PriceData
from tests.support import (
    PRODUCT_HTML,
    Harness,
    create_api_key,
    sample_results,
)


async def test_search_returns_ranked_results(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/search",
        json={"query": "  laptop  ", "limit": 2, "country": "BD"},
        headers={"X-Request-ID": "test-request-1"},
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request-1"
    body = response.json()
    assert body["success"] is True
    assert body["metadata"]["query"] == "laptop"
    assert body["metadata"]["country"] == "BD"
    assert body["metadata"]["request_id"] == "test-request-1"
    assert body["metadata"]["total_results"] == 2
    assert len(body["results"]) == 2
    assert body["metadata"]["sources_used"] == ["fake"]
    assert body["metadata"]["source_statuses"][0]["status"] == "ok"
    assert body["results"][0]["source"] == "fake"


async def test_search_cache_hit_and_bypass(harness: Harness) -> None:
    payload = {"query": "cache test laptop"}
    first = await harness.client.post("/v1/search", json=payload)
    second = await harness.client.post("/v1/search", json=payload)
    bypass = await harness.client.post("/v1/search", json={**payload, "use_cache": False})
    assert first.status_code == 200
    assert first.json()["metadata"]["cached"] is False
    assert second.json()["metadata"]["cached"] is True
    assert bypass.json()["metadata"]["cached"] is False
    assert harness.adapter.calls == 2


async def test_domain_filters(harness: Harness) -> None:
    response = await harness.client.post(
        "/v1/search",
        json={"query": "laptop", "include_domains": ["example.org"]},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert all(result["domain"] == "example.org" for result in results)


async def test_unknown_source_returns_503(harness: Harness) -> None:
    response = await harness.client.post("/v1/search", json={"query": "laptop", "sources": ["missing"]})
    assert response.status_code == 503
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "search_source_unavailable"


async def test_all_adapters_failing_returns_503(harness: Harness) -> None:
    harness.adapter.error = SourceTimeoutError("upstream timed out")
    response = await harness.client.post("/v1/search", json={"query": "laptop"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "search_source_unavailable"
    details = response.json()["error"]["details"]
    assert details[0]["status"] == "timeout"


async def test_search_validation_error(harness: Harness) -> None:
    response = await harness.client.post("/v1/search", json={"query": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["request_id"]


async def test_deep_search_extracts_top_result(harness: Harness) -> None:
    harness.http.html = PRODUCT_HTML
    response = await harness.client.post(
        "/v1/search",
        json={"query": "laptop", "mode": "deep", "extract_limit": 1},
    )
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["extraction_status"] == "success"
    assert results[0]["extraction"]["product"]["name"] == "Acme UltraBook 14"
    assert harness.http.calls


async def test_async_search_creates_job(harness: Harness) -> None:
    response = await harness.client.post("/v1/search", json={"query": "laptop", "async": True})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["job_id"]
    assert harness.arq.enqueued[0]["function"] == "process_search_job"
    assert harness.arq.enqueued[0]["kwargs"]["_queue_name"] == "serp:search"

    status = await harness.client.get(body["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


async def test_product_price_filter(harness: Harness) -> None:
    results = sample_results()[:2]
    results[0].snippet_price = PriceData(amount=55_000, currency="BDT")
    results[1].snippet_price = PriceData(amount=20_000, currency="BDT")
    harness.adapter.results = results

    response = await harness.client.post(
        "/v1/products/search",
        json={"query": "laptop", "min_price": 30_000, "currency": "BDT"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["total_results"] == 1
    assert body["results"][0]["snippet_price"]["amount"] == 55_000


async def test_missing_and_invalid_api_keys(harness: Harness, anon_client: object) -> None:
    missing = await anon_client.post("/v1/search", json={"query": "laptop"})
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "authentication_failed"

    invalid = await anon_client.post(
        "/v1/search",
        json={"query": "laptop"},
        headers={"X-API-Key": "sk_test_invalid_key_value"},
    )
    assert invalid.status_code == 401


async def test_revoked_and_expired_keys(harness: Harness) -> None:
    _, _, _, revoked = await create_api_key(harness.db, harness.settings, name="revoked", is_active=False)
    revoked_response = await harness.client.get(
        "/v1/account",
        headers={"X-API-Key": revoked},
    )
    assert revoked_response.status_code == 403
    assert revoked_response.json()["error"]["code"] == "forbidden"

    _, _, _, expired = await create_api_key(
        harness.db,
        harness.settings,
        name="expired",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    expired_response = await harness.client.get("/v1/account", headers={"X-API-Key": expired})
    assert expired_response.status_code == 401
    assert expired_response.json()["error"]["code"] == "authentication_failed"


async def test_rate_limit(harness: Harness) -> None:
    _, _, _, raw = await create_api_key(harness.db, harness.settings, name="limited", rate_limit=1)
    first = await harness.client.get("/v1/account", headers={"X-API-Key": raw})
    second = await harness.client.get("/v1/account", headers={"X-API-Key": raw})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limit_exceeded"


async def test_daily_quota(harness: Harness) -> None:
    _, _, _, raw = await create_api_key(harness.db, harness.settings, name="quota", daily_quota=1)
    first = await harness.client.get("/v1/account", headers={"X-API-Key": raw})
    second = await harness.client.get("/v1/account", headers={"X-API-Key": raw})
    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "quota_exceeded"


async def test_request_id_is_sanitized_and_security_headers_present(harness: Harness) -> None:
    response = await harness.client.get("/v1/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]
    assert " " not in request_id
    assert len(request_id) == 32
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"

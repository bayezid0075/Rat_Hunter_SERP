from __future__ import annotations

from uuid import uuid4

from tests.support import Harness


async def test_health_and_ready(harness: Harness, anon_client: object) -> None:
    health = await anon_client.get("/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert "api" in body["components"]

    ready = await anon_client.get("/v1/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True
    assert ready.json()["checks"] == {"database": True, "cache": True}


async def test_metrics_endpoint(harness: Harness, anon_client: object) -> None:
    await anon_client.get("/v1/health")
    response = await anon_client.get("/metrics")
    assert response.status_code == 200
    assert "serp_api_requests_total" in response.text
    assert "serp_api_request_duration_seconds" in response.text


async def test_account_details(harness: Harness) -> None:
    response = await harness.client.get("/v1/account")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(harness.account_id)
    assert body["name"] == "Test Account"
    assert body["plan"] == "free"
    assert body["api_key"]["id"] == str(harness.api_key_id)
    assert body["api_key"]["key_prefix"] == harness.raw_key[:16]
    assert body["application"]["name"] == "test app"


async def test_usage_statistics(harness: Harness) -> None:
    await harness.client.post("/v1/search", json={"query": "usage test"})
    response = await harness.client.get("/v1/account/usage?days=7")
    assert response.status_code == 200
    body = response.json()
    assert body["account_id"] == str(harness.account_id)
    assert body["stats"]["total_requests"] >= 1
    assert body["stats"]["quota"] == 1_000
    assert any(item["endpoint"].endswith("/search") for item in body["stats"]["by_endpoint"])


async def test_sources_list_and_health(harness: Harness) -> None:
    sources = await harness.client.get("/v1/sources")
    assert sources.status_code == 200
    body = sources.json()
    assert len(body) == 1
    assert body[0]["name"] == "fake"
    assert body[0]["adapter_type"] == "fake"
    assert body[0]["capabilities"] == ["web", "products", "news"]

    health = await harness.client.get("/v1/sources/health")
    assert health.status_code == 200
    health_body = health.json()
    assert len(health_body["sources"]) == 1
    assert health_body["sources"][0]["name"] == "fake"
    assert health_body["sources"][0]["status"] == "ok"


async def test_missing_job_returns_404(harness: Harness) -> None:
    response = await harness.client.get(f"/v1/jobs/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"

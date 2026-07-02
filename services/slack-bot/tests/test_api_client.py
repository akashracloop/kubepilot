"""Tests for the async API-gateway client, using httpx.MockTransport."""

from __future__ import annotations

import json

import httpx
from kubepilot_slack.api_client import InvestigationApiClient

INCIDENT_ID = "33333333-3333-3333-3333-333333333333"


async def test_start_investigation_posts_with_api_key_and_returns_id() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["api_key"] = request.headers.get("X-API-Key")
        seen["json"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "incident_id": INCIDENT_ID,
                "status": "queued",
                "created_at": "2026-07-02T00:00:00Z",
            },
        )

    client = InvestigationApiClient(
        api_url="http://gw.local",
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
    )
    try:
        incident_id = await client.start_investigation(
            query="why is payment-service failing",
            namespace="prod",
            service="payment-service",
        )
    finally:
        await client.aclose()

    assert incident_id == INCIDENT_ID
    assert seen["method"] == "POST"
    assert seen["path"] == "/investigations"
    assert seen["api_key"] == "secret-key"
    assert seen["json"] == {
        "query": "why is payment-service failing",
        "namespace": "prod",
        "service": "payment-service",
    }


async def test_no_api_key_header_when_unset() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["has_key"] = "X-API-Key" in request.headers
        return httpx.Response(202, json={"incident_id": INCIDENT_ID, "status": "queued"})

    client = InvestigationApiClient(
        api_url="http://gw.local",
        transport=httpx.MockTransport(handler),
    )
    try:
        await client.start_investigation(query="q", namespace="prod")
    finally:
        await client.aclose()

    assert captured["has_key"] is False


async def test_wait_for_polls_until_completed() -> None:
    calls = {"n": 0}
    statuses = ["running", "running", "completed"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/investigations/{INCIDENT_ID}"
        status = statuses[min(calls["n"], len(statuses) - 1)]
        calls["n"] += 1
        return httpx.Response(200, json={"incident_id": INCIDENT_ID, "status": status, "state": {}})

    client = InvestigationApiClient(
        api_url="http://gw.local",
        transport=httpx.MockTransport(handler),
    )
    try:
        detail = await client.wait_for(INCIDENT_ID, timeout=5.0, poll_interval=0.01)
    finally:
        await client.aclose()

    assert detail["status"] == "completed"
    assert calls["n"] == 3  # polled twice while running, then terminal


async def test_get_investigation_returns_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"incident_id": INCIDENT_ID, "status": "completed"})

    client = InvestigationApiClient(
        api_url="http://gw.local",
        transport=httpx.MockTransport(handler),
    )
    try:
        detail = await client.get_investigation(INCIDENT_ID)
    finally:
        await client.aclose()

    assert detail["incident_id"] == INCIDENT_ID

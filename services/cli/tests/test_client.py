"""Client tests — drive the HTTP seam with an in-memory ``httpx.MockTransport``."""

from __future__ import annotations

import json

import httpx
import pytest
from kubepilot_cli import client
from kubepilot_cli.config import Settings

SETTINGS = Settings(api_url="http://test", api_key="secret-key")


async def test_create_posts_with_api_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["api_key"] = request.headers.get("X-API-Key")
        seen["payload"] = json.loads(request.content)
        return httpx.Response(
            202,
            json={
                "incident_id": "11111111-2222-3333-4444-555555555555",
                "status": "pending",
                "created_at": "2026-07-02T00:00:00Z",
            },
        )

    transport = httpx.MockTransport(handler)
    result = await client.create(
        "why is api failing?", "prod", "api", 30, settings=SETTINGS, transport=transport
    )

    assert seen["method"] == "POST"
    assert seen["path"] == "/investigations"
    assert seen["api_key"] == "secret-key"
    assert seen["payload"] == {
        "query": "why is api failing?",
        "namespace": "prod",
        "time_window_minutes": 30,
        "service": "api",
    }
    assert result["incident_id"] == "11111111-2222-3333-4444-555555555555"


async def test_wait_for_polls_until_completed() -> None:
    statuses = iter(["running", "running", "completed"])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/investigations/abc"
        return httpx.Response(200, json={"incident_id": "abc", "status": next(statuses)})

    transport = httpx.MockTransport(handler)
    detail = await client.wait_for(
        "abc", timeout=5.0, poll=0.0, settings=SETTINGS, transport=transport
    )
    assert detail["status"] == "completed"


async def test_list_sends_pagination_params() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["limit"] = request.url.params.get("limit")
        seen["offset"] = request.url.params.get("offset")
        return httpx.Response(200, json={"items": [], "limit": 20, "offset": 5})

    transport = httpx.MockTransport(handler)
    result = await client.list(limit=20, offset=5, settings=SETTINGS, transport=transport)

    assert seen["path"] == "/investigations"
    assert seen["limit"] == "20"
    assert seen["offset"] == "5"
    assert result["limit"] == 20


async def test_error_response_raises_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    with pytest.raises(client.ApiError, match="not found"):
        await client.get("missing", settings=SETTINGS, transport=transport)

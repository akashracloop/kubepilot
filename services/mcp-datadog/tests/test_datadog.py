"""mcp-datadog — curated mapping + read-only contract (Phase 3 W11).

No live Datadog: a mocked httpx transport feeds Datadog-shaped payloads and we
assert they map into KubePilot's curated MetricSeries / LogLine shapes, and that
the server exposes only read-only query/search tools.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from mcp_datadog import client
from mcp_datadog.server import app
from mcp_datadog.tools import REGISTRY
from mcp_datadog.tools.logs import search_logs
from mcp_datadog.tools.metrics import query_metrics

_DD_METRICS = {
    "series": [
        {
            "metric": "system.mem.used",
            "scope": "service:checkout,host:h1",
            "pointlist": [[1720000000000, 1.5], [1720000030000, 2.0], [1720000060000, None]],
        }
    ]
}

_DD_LOGS = {
    "data": [
        {
            "attributes": {
                "message": "java.lang.OutOfMemoryError: Java heap space",
                "timestamp": "2026-07-02T10:08:00Z",
                "service": "checkout",
                "status": "error",
            }
        }
    ]
}


def _mock_datadog(metrics: dict | None = None, logs: dict | None = None) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/query":
            return httpx.Response(200, json=metrics or {"series": []})
        if request.url.path == "/api/v2/logs/events/search":
            return httpx.Response(200, json=logs or {"data": []})
        return httpx.Response(404, json={"errors": ["not found"]})

    client.set_client(
        httpx.AsyncClient(
            transport=httpx.MockTransport(handler), base_url="https://api.datadoghq.com"
        )
    )


@pytest.mark.asyncio
async def test_query_metrics_maps_to_curated_series() -> None:
    _mock_datadog(metrics=_DD_METRICS)
    result = await query_metrics("avg:system.mem.used{service:checkout}")
    assert len(result.series) == 1
    series = result.series[0]
    assert series.labels["service"] == "checkout"
    assert series.labels["host"] == "h1"
    assert series.labels["__name__"] == "system.mem.used"
    # Null point dropped; two numeric samples remain, tz-aware.
    assert [s.value for s in series.samples] == [1.5, 2.0]
    assert series.samples[0].timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_search_logs_maps_to_curated_lines() -> None:
    _mock_datadog(logs=_DD_LOGS)
    result = await search_logs("service:checkout status:error")
    assert result.total_lines == 1
    line = result.lines[0]
    assert "OutOfMemoryError" in line.line
    assert line.stream_labels["service"] == "checkout"
    assert line.stream_labels["status"] == "error"
    assert line.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_server_health_and_tools() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dd") as c:
        health = await c.get("/mcp/health")
        assert health.status_code == 200
        assert health.json()["server"] == "mcp-datadog"

        tools = (await c.get("/mcp/tools")).json()["tools"]
        names = {t["name"] for t in tools}
        assert names == {"query_metrics", "search_logs"}


@pytest.mark.asyncio
async def test_server_invoke_returns_curated_result() -> None:
    _mock_datadog(logs=_DD_LOGS)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dd") as c:
        resp = await c.post(
            "/mcp/invoke",
            json={"tool": "search_logs", "arguments": {"query": "status:error"}},
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["lines"][0]["stream_labels"]["service"] == "checkout"


@pytest.mark.asyncio
async def test_unknown_tool_404() -> None:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dd") as c:
        resp = await c.post("/mcp/invoke", json={"tool": "delete_monitor"})
        assert resp.status_code == 404


def test_readonly_posture_no_write_tools() -> None:
    """Contract: the Datadog adapter exposes only read-only query/search tools."""
    write_verbs = ("create", "update", "delete", "post_", "mute", "put", "patch", "remove")
    for name in REGISTRY.tools:
        assert not any(name.lower().startswith(v) or v in name.lower() for v in write_verbs), name

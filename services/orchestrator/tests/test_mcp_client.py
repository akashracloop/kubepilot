"""Tests for the orchestrator's HTTP client to MCP servers."""

from __future__ import annotations

import httpx
import pytest
from kubepilot_orch.mcp.client import MCPClient, MCPError


def _make_client(handler) -> MCPClient:  # type: ignore[no-untyped-def]
    """Build an MCPClient whose underlying httpx uses MockTransport with `handler`."""
    transport = httpx.MockTransport(handler)
    client = MCPClient(server_name="mcp-k8s", base_url="http://mcp-k8s")
    # Swap the AsyncClient for one with our mock transport.
    client._http = httpx.AsyncClient(transport=transport, base_url="http://mcp-k8s")
    return client


@pytest.mark.asyncio
async def test_health_returns_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/health"
        return httpx.Response(200, json={"status": "ok", "server": "mcp-k8s", "version": "0.1.0"})

    async with _make_client(handler) as c:
        h = await c.health()
        assert h["status"] == "ok"


@pytest.mark.asyncio
async def test_list_tools_caches_descriptors() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(
            200,
            json={
                "tools": [
                    {
                        "name": "list_pods",
                        "description": "...",
                        "parameters": {"type": "object", "properties": {}},
                    }
                ]
            },
        )

    async with _make_client(handler) as c:
        first = await c.list_tools()
        second = await c.list_tools()
        assert [t.name for t in first] == ["list_pods"]
        assert [t.name for t in second] == ["list_pods"]
        assert call_count["n"] == 1, "list_tools() should cache after the first call"


@pytest.mark.asyncio
async def test_list_tools_refresh_bypasses_cache() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={"tools": []})

    async with _make_client(handler) as c:
        await c.list_tools()
        await c.list_tools(refresh=True)
        assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_invoke_returns_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/mcp/invoke"
        return httpx.Response(200, json={"tool": "list_pods", "result": [{"name": "p1"}]})

    async with _make_client(handler) as c:
        result = await c.invoke("list_pods", {"namespace": "prod"})
        assert result == [{"name": "p1"}]


@pytest.mark.asyncio
async def test_invoke_4xx_raises_mcp_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "missing required argument: namespace"})

    async with _make_client(handler) as c:
        with pytest.raises(MCPError) as excinfo:
            await c.invoke("list_pods", {})
        assert excinfo.value.status == 400
        assert excinfo.value.tool == "list_pods"
        assert "missing required argument" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_invoke_502_raises_mcp_error_with_upstream_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            502,
            json={"detail": {"k8s_status": 403, "reason": "Forbidden", "tool": "list_pods"}},
        )

    async with _make_client(handler) as c:
        with pytest.raises(MCPError) as excinfo:
            await c.invoke("list_pods", {"namespace": "prod"})
        assert excinfo.value.status == 502
        assert isinstance(excinfo.value.detail, dict)
        assert excinfo.value.detail["k8s_status"] == 403


@pytest.mark.asyncio
async def test_invoke_retries_on_transport_error() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("flaky network")
        return httpx.Response(200, json={"tool": "x", "result": "ok"})

    async with _make_client(handler) as c:
        result = await c.invoke("x")
        assert result == "ok"
        assert attempts["n"] == 3  # 2 failures then a success — tenacity retried correctly

"""Tests for the MCP server HTTP endpoints (routing, error handling)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mcp_k8s.server import app


def test_health() -> None:
    r = TestClient(app).get("/mcp/health")
    assert r.status_code == 200
    body = r.json()
    assert body["server"] == "mcp-k8s"
    assert body["status"] == "ok"


def test_list_tools_includes_all_phase1_tools() -> None:
    r = TestClient(app).get("/mcp/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}

    expected = {
        "list_pods",
        "describe_pod",
        "get_events",
        "get_nodes",
        "get_deployments",
        "get_services",
        "get_pvcs",
        "get_configmap",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


def test_tool_descriptors_have_json_schemas() -> None:
    r = TestClient(app).get("/mcp/tools")
    tools = r.json()["tools"]
    for t in tools:
        assert t.get("name")
        assert t.get("description")
        assert "parameters" in t
        assert t["parameters"]["type"] == "object"


def test_unknown_tool_returns_404() -> None:
    r = TestClient(app).post("/mcp/invoke", json={"tool": "definitely_not_a_tool", "arguments": {}})
    assert r.status_code == 404
    assert "Unknown tool" in r.json()["detail"]


def test_missing_required_argument_returns_400(core_v1) -> None:  # type: ignore[no-untyped-def]
    """The handler requires ``namespace`` — calling without it must 400, not crash."""
    r = TestClient(app).post("/mcp/invoke", json={"tool": "list_pods", "arguments": {}})
    assert r.status_code == 400

"""mcp-prom server routing and error handling."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mcp_prom.server import app


def test_list_tools_includes_all_phase1_tools() -> None:
    r = TestClient(app).get("/mcp/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert {"query_metrics", "query_range", "list_targets", "query_alerts"}.issubset(names)


def test_tool_descriptors_have_json_schemas() -> None:
    r = TestClient(app).get("/mcp/tools")
    for t in r.json()["tools"]:
        assert t.get("name")
        assert t.get("description")
        assert t["parameters"]["type"] == "object"


def test_unknown_tool_returns_404() -> None:
    r = TestClient(app).post("/mcp/invoke", json={"tool": "ghost", "arguments": {}})
    assert r.status_code == 404


def test_missing_required_argument_returns_400() -> None:
    r = TestClient(app).post("/mcp/invoke", json={"tool": "query_metrics", "arguments": {}})
    assert r.status_code == 400

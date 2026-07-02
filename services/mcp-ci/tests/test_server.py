"""mcp-ci server routing and error handling."""

from __future__ import annotations

from fastapi.testclient import TestClient
from mcp_ci.models import DeploymentHistory
from mcp_ci.server import app


def test_list_tools_includes_all_phase2_tools() -> None:
    r = TestClient(app).get("/mcp/tools")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert names == {"get_deployment_history", "get_recent_commits", "get_pipeline_status"}


def test_tool_descriptors_have_json_schemas() -> None:
    r = TestClient(app).get("/mcp/tools")
    for t in r.json()["tools"]:
        assert t.get("name")
        assert t.get("description")
        assert t["parameters"]["type"] == "object"
        assert t["parameters"]["additionalProperties"] is False


def test_unknown_tool_returns_404() -> None:
    r = TestClient(app).post("/mcp/invoke", json={"tool": "ghost", "arguments": {}})
    assert r.status_code == 404


def test_missing_required_argument_returns_400() -> None:
    r = TestClient(app).post(
        "/mcp/invoke", json={"tool": "get_deployment_history", "arguments": {}}
    )
    assert r.status_code == 400


def test_invoke_dispatches_to_backend(backend) -> None:  # type: ignore[no-untyped-def]
    backend.deployments = DeploymentHistory(
        service="payment-service", window_minutes=60, deployments=[]
    )
    r = TestClient(app).post(
        "/mcp/invoke",
        json={"tool": "get_deployment_history", "arguments": {"service": "payment-service"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["tool"] == "get_deployment_history"
    assert body["result"]["service"] == "payment-service"
    assert backend.calls[0]["method"] == "deployment_history"


def test_upstream_error_maps_to_502(backend) -> None:  # type: ignore[no-untyped-def]
    from mcp_ci.client import CIError

    async def boom(repo_or_service: str):  # type: ignore[no-untyped-def]
        raise CIError(status=503, body="upstream down")

    backend.pipeline_status = boom  # type: ignore[assignment]
    r = TestClient(app).post(
        "/mcp/invoke",
        json={"tool": "get_pipeline_status", "arguments": {"repo_or_service": "acme/web"}},
    )
    assert r.status_code == 502
    assert r.json()["detail"]["upstream_status"] == 503

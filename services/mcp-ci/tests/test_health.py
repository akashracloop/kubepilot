from __future__ import annotations

from fastapi.testclient import TestClient
from mcp_ci.server import app


def test_health() -> None:
    r = TestClient(app).get("/mcp/health")
    assert r.status_code == 200
    assert r.json()["server"] == "mcp-ci"

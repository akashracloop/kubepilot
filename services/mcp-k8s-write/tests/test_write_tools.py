"""mcp-k8s-write — dry-run-only posture + safe allow-list (Phase 4 W1).

No real cluster: the server computes dry-run previews and applies nothing. These
tests lock in that the write server cannot mutate a cluster in W1 and that its
tool surface is curated + reversible-leaning.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from mcp_k8s_write.safety import PARTIAL, REVERSIBLE, WRITE_TOOLS, required_rbac
from mcp_k8s_write.server import app


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://write")


@pytest.mark.asyncio
async def test_health_reports_dry_run_only() -> None:
    async with await _client() as c:
        body = (await c.get("/mcp/health")).json()
    assert body["server"] == "mcp-k8s-write"
    assert body["mode"] == "dry-run-only"
    assert body["apply_enabled"] is False  # default off switch


@pytest.mark.asyncio
async def test_tools_list_is_the_curated_write_surface() -> None:
    async with await _client() as c:
        tools = (await c.get("/mcp/tools")).json()["tools"]
    names = {t["name"] for t in tools}
    assert names == set(WRITE_TOOLS)
    # Every tool advertises its reversibility + approval tier.
    for t in tools:
        assert t["reversibility"] in (REVERSIBLE, PARTIAL)
        assert t["approval_tier"] in ("operator", "admin")


@pytest.mark.asyncio
async def test_invoke_is_dry_run_and_applies_nothing() -> None:
    async with await _client() as c:
        resp = await c.post(
            "/mcp/invoke",
            json={
                "tool": "rollout_undo",
                "arguments": {"namespace": "prod", "target": "deployment/checkout"},
            },
        )
    assert resp.status_code == 200
    result = resp.json()["result"]
    assert result["dry_run"] is True
    assert result["applied"] is False
    assert "dry run" in result["preview"]
    assert result["reversibility"] == "reversible"


@pytest.mark.asyncio
async def test_apply_request_is_refused_and_warned() -> None:
    """Even an explicit apply (dry_run=false) applies nothing in W1."""
    async with await _client() as c:
        resp = await c.post(
            "/mcp/invoke",
            json={
                "tool": "scale",
                "arguments": {"namespace": "prod", "target": "deployment/checkout", "replicas": 5},
                "dry_run": False,
            },
        )
    result = resp.json()["result"]
    assert result["applied"] is False
    assert result["dry_run"] is True
    assert any("dry-run-only" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_unknown_tool_fails_closed() -> None:
    async with await _client() as c:
        resp = await c.post("/mcp/invoke", json={"tool": "delete_namespace", "arguments": {}})
    assert resp.status_code == 404


def test_allow_list_has_no_destructive_footprint() -> None:
    """The write surface must never grant delete on persistent/secret resources."""
    rbac = required_rbac()
    forbidden = {
        "/persistentvolumeclaims",
        "/persistentvolumes",
        "/secrets",
        "/namespaces",
    }
    for key, verbs in rbac.items():
        assert key not in forbidden, f"write surface must not touch {key}"
        assert "deletecollection" not in verbs
    # No tool is irreversible.
    assert all(s.reversibility in (REVERSIBLE, PARTIAL) for s in WRITE_TOOLS.values())

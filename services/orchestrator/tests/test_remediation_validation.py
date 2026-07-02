"""Post-remediation validation — confirm/deny + rollback-on-regression (Phase 4 W9)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kubepilot_orch.remediation import validation
from kubepilot_orch.state import ExecutionRecord
from kubepilot_orch.testing import build_mcp_client


def _write_mcp() -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json={"tools": []})
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"applied": True}})

    return build_mcp_client(handler, server_name="mcp-k8s-write")


def _exec(tool: str = "scale") -> ExecutionRecord:
    return ExecutionRecord(
        action_index=0,
        tool=tool,
        target="deployment/x",
        namespace="prod",
        status="succeeded",
        dry_run=False,
        pre_state={"replicas": 3},
    )


# ---- assess_outcome --------------------------------------------------------


def test_improved_when_error_rate_drops() -> None:
    assert validation.assess_outcome({"error_rate": 0.30}, {"error_rate": 0.02}) == "improved"


def test_regressed_when_worse() -> None:
    assert validation.assess_outcome({"error_rate": 0.02}, {"error_rate": 0.40}) == "regressed"


def test_unchanged_when_flat() -> None:
    assert validation.assess_outcome({"error_rate": 0.20}, {"error_rate": 0.19}) == "unchanged"


# ---- finalize_remediation --------------------------------------------------


@pytest.mark.asyncio
async def test_improved_closes_without_rollback() -> None:
    mcp = _write_mcp()
    res = await validation.finalize_remediation(
        [_exec()], {"error_rate": 0.3}, {"error_rate": 0.01}, mcp_write=mcp
    )
    await mcp.aclose()
    assert res.outcome == "closed"
    assert res.kind == "improved"
    assert res.rollbacks == []


@pytest.mark.asyncio
async def test_regression_rolls_back_and_reopens() -> None:
    mcp = _write_mcp()
    res = await validation.finalize_remediation(
        [_exec("scale")], {"error_rate": 0.02}, {"error_rate": 0.50}, mcp_write=mcp
    )
    await mcp.aclose()
    assert res.outcome == "reopened"
    assert res.kind == "regressed"
    assert len(res.rollbacks) == 1 and res.rollbacks[0].status == "succeeded"


@pytest.mark.asyncio
async def test_unchanged_reopens_without_rollback() -> None:
    mcp = _write_mcp()
    res = await validation.finalize_remediation(
        [_exec()], {"error_rate": 0.20}, {"error_rate": 0.20}, mcp_write=mcp
    )
    await mcp.aclose()
    assert res.outcome == "reopened"
    assert res.kind == "unchanged"
    assert res.rollbacks == []

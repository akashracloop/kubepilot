"""Auto-rollback — regression detection + inverse actions + execute (Phase 4 W8)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kubepilot_orch.remediation import rollback
from kubepilot_orch.state import ExecutionRecord
from kubepilot_orch.testing import build_mcp_client
from structlog.testing import capture_logs


def _exec(
    tool: str, pre_state: dict | None = None, status: str = "succeeded", idx: int = 0
) -> ExecutionRecord:  # type: ignore[type-arg]
    return ExecutionRecord(
        action_index=idx,
        tool=tool,
        target=f"deployment/{tool}-tgt",
        namespace="prod",
        status=status,
        dry_run=False,
        pre_state=pre_state,
    )


def _write_mcp(fail: bool = False) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json={"tools": []})
        if fail:
            return httpx.Response(502, json={"detail": "boom"})
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"applied": True}})

    return build_mcp_client(handler, server_name="mcp-k8s-write")


# ---- regression detection --------------------------------------------------


def test_regression_on_error_rate_spike() -> None:
    assert rollback.assess_regression({"error_rate": 0.01}, {"error_rate": 0.30}) is True


def test_regression_on_new_restarts() -> None:
    assert rollback.assess_regression({"restarts": 3}, {"restarts": 5}) is True


def test_no_regression_when_stable() -> None:
    assert (
        rollback.assess_regression(
            {"error_rate": 0.02, "restarts": 3}, {"error_rate": 0.02, "restarts": 3}
        )
        is False
    )


# ---- inverse actions -------------------------------------------------------


def test_inverse_scale_restores_replicas() -> None:
    inv = rollback.inverse_action(_exec("scale", pre_state={"replicas": 3}))
    assert inv is not None and inv.tool == "scale" and inv.arguments == {"replicas": 3}


def test_inverse_patch_image_restores_image() -> None:
    inv = rollback.inverse_action(
        _exec("patch_image", pre_state={"image": "svc:v1", "container": "app"})
    )
    assert inv is not None and inv.arguments == {"image": "svc:v1", "container": "app"}


def test_cordon_and_uncordon_are_self_inverting() -> None:
    assert rollback.inverse_action(_exec("cordon")).tool == "uncordon"  # type: ignore[union-attr]
    assert rollback.inverse_action(_exec("uncordon")).tool == "cordon"  # type: ignore[union-attr]


def test_non_revertible_actions_have_no_inverse() -> None:
    assert rollback.inverse_action(_exec("rollout_undo")) is None
    assert rollback.inverse_action(_exec("rollout_restart")) is None
    assert rollback.inverse_action(_exec("restart_pod")) is None
    # scale without a captured pre-state can't be reverted.
    assert rollback.inverse_action(_exec("scale", pre_state=None)) is None


# ---- auto_rollback ---------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_rollback_reverts_on_regression_and_audits() -> None:
    mcp = _write_mcp()
    execs = [_exec("scale", pre_state={"replicas": 3})]
    with capture_logs() as logs:
        rbs = await rollback.auto_rollback(execs, mcp_write=mcp, regressed=True)
    await mcp.aclose()
    assert len(rbs) == 1 and rbs[0].status == "succeeded"
    assert any(
        e.get("audit") and e["action"] == "auto_rollback" and e["decision"] == "rolled_back"
        for e in logs
    )


@pytest.mark.asyncio
async def test_no_rollback_when_not_regressed() -> None:
    mcp = _write_mcp()
    rbs = await rollback.auto_rollback(
        [_exec("scale", pre_state={"replicas": 3})], mcp_write=mcp, regressed=False
    )
    await mcp.aclose()
    assert rbs == []


@pytest.mark.asyncio
async def test_non_revertible_actions_are_not_rolled_back() -> None:
    mcp = _write_mcp()
    rbs = await rollback.auto_rollback([_exec("rollout_undo")], mcp_write=mcp, regressed=True)
    await mcp.aclose()
    assert rbs == []


@pytest.mark.asyncio
async def test_rollback_failure_is_recorded() -> None:
    mcp = _write_mcp(fail=True)
    rbs = await rollback.auto_rollback([_exec("cordon")], mcp_write=mcp, regressed=True)
    await mcp.aclose()
    assert rbs[0].status == "failed"

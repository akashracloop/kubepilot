"""Remediation execution engine — policy + kill switch + audit (Phase 4 W7)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from kubepilot_orch.remediation import approval, executor
from kubepilot_orch.remediation.policy import load_policies_from_yaml
from kubepilot_orch.state import Approval, BlastRadius, RemediationAction, RemediationPlan
from kubepilot_orch.testing import build_mcp_client
from structlog.testing import capture_logs

_POLICY = load_policies_from_yaml(
    """
policies:
  - name: prod-rollback
    roles: [operator, admin]
    namespaces: [prod]
    actions: [rollout_undo]
    reversibility: [reversible]
    max_blast_radius: { pods: 10 }
"""
)


def _write_mcp(applied: bool = False, fail: bool = False) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json={"tools": []})
        if fail:
            return httpx.Response(502, json={"detail": "boom"})
        body = json.loads(request.content.decode())
        return httpx.Response(
            200,
            json={
                "tool": body["tool"],
                "result": {
                    "applied": applied,
                    "dry_run": not applied,
                    "preview": "would roll back",
                },
            },
        )

    return build_mcp_client(handler, server_name="mcp-k8s-write")


def _plan() -> RemediationPlan:
    return RemediationPlan(
        actions=[
            RemediationAction(
                tool="rollout_undo",
                target="deployment/checkout",
                namespace="prod",
                reversibility="reversible",
                approval_tier="operator",
                estimated_blast_radius=BlastRadius(pods_affected=3, traffic_percent=100.0),
            )
        ]
    )


def _approved() -> list[Approval]:
    return [approval.build_approval(action_index=0, decision="approved", approver_role="operator")]


@pytest.fixture(autouse=True)
def _reset_kill_switch():  # type: ignore[no-untyped-def]
    executor.set_kill_switch(False)
    yield
    executor.set_kill_switch(False)


@pytest.mark.asyncio
async def test_executes_approved_in_policy_action() -> None:
    mcp = _write_mcp(applied=True)
    with capture_logs() as logs:
        recs = await executor.execute_plan(_plan(), _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert len(recs) == 1
    assert recs[0].status == "succeeded"
    assert recs[0].dry_run is False
    assert any(e.get("audit") and e["decision"] == "executed" for e in logs)


@pytest.mark.asyncio
async def test_dry_run_when_write_server_applies_nothing() -> None:
    mcp = _write_mcp(applied=False)  # W1/W7 write server: dry-run only
    recs = await executor.execute_plan(_plan(), _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert recs[0].status == "dry_run"
    assert recs[0].dry_run is True


@pytest.mark.asyncio
async def test_kill_switch_halts_execution() -> None:
    executor.set_kill_switch(True)
    mcp = _write_mcp(applied=True)
    with capture_logs() as logs:
        recs = await executor.execute_plan(_plan(), _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert recs[0].status == "skipped"
    assert "kill switch" in recs[0].output
    assert any(e.get("audit") and e["reason"] == "kill_switch" for e in logs)


@pytest.mark.asyncio
async def test_policy_denied_action_is_skipped() -> None:
    # A plan in a namespace the policy doesn't allow.
    plan = _plan()
    plan.actions[0].namespace = "staging"
    plan.actions[0].estimated_blast_radius = BlastRadius(pods_affected=3)
    mcp = _write_mcp(applied=True)
    with capture_logs() as logs:
        recs = await executor.execute_plan(plan, _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert recs[0].status == "skipped"
    assert "policy denied" in recs[0].output
    assert any(e.get("audit") and e["reason"] == "policy_denied" for e in logs)


@pytest.mark.asyncio
async def test_no_policy_denies_everything() -> None:
    mcp = _write_mcp(applied=True)
    recs = await executor.execute_plan(_plan(), _approved(), mcp_write=mcp, policy=None)
    await mcp.aclose()
    assert recs[0].status == "skipped"


@pytest.mark.asyncio
async def test_blast_radius_over_cap_is_skipped() -> None:
    plan = _plan()
    plan.actions[0].estimated_blast_radius = BlastRadius(pods_affected=50)  # over the 10 cap
    mcp = _write_mcp(applied=True)
    recs = await executor.execute_plan(plan, _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert recs[0].status == "skipped"


@pytest.mark.asyncio
async def test_mcp_error_records_failed_not_success() -> None:
    mcp = _write_mcp(fail=True)
    with capture_logs() as logs:
        recs = await executor.execute_plan(_plan(), _approved(), mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert recs[0].status == "failed"
    assert any(e.get("audit") and e["decision"] == "failed" for e in logs)


@pytest.mark.asyncio
async def test_only_approved_indices_execute() -> None:
    plan = RemediationPlan(
        actions=[
            RemediationAction(
                tool="rollout_undo",
                target="deployment/a",
                namespace="prod",
                estimated_blast_radius=BlastRadius(pods_affected=1),
            ),
            RemediationAction(
                tool="rollout_undo",
                target="deployment/b",
                namespace="prod",
                estimated_blast_radius=BlastRadius(pods_affected=1),
            ),
        ]
    )
    approvals = [
        approval.build_approval(action_index=1, decision="approved", approver_role="operator")
    ]
    mcp = _write_mcp(applied=True)
    recs = await executor.execute_plan(plan, approvals, mcp_write=mcp, policy=_POLICY)
    await mcp.aclose()
    assert [r.action_index for r in recs] == [1]  # only the approved one ran

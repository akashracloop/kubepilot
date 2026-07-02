"""Self-healing — pattern matching + opt-in + still fully gated (Phase 4 W10)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from kubepilot_orch.remediation import executor, selfheal
from kubepilot_orch.remediation.policy import load_policies_from_yaml
from kubepilot_orch.state import InvestigationState, RCAReport
from kubepilot_orch.testing import build_mcp_client

_POLICY = load_policies_from_yaml(
    """
policies:
  - name: selfheal
    roles: [operator, admin]
    namespaces: [prod]
    actions: [rollout_undo, restart_pod]
    reversibility: [reversible]
    max_blast_radius: { pods: 5 }
"""
)


def _state(category: str) -> InvestigationState:
    return InvestigationState(
        incident_id=uuid.uuid4(),
        query="why is checkout failing?",
        namespace="prod",
        service="checkout",
        rca=RCAReport(root_cause="x", root_cause_category=category, confidence=0.8, reasoning="y"),
        started_at=datetime(2026, 7, 2, 10, 0, tzinfo=UTC),
    )


def _write_mcp() -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mcp/tools":
            return httpx.Response(200, json={"tools": []})
        body = json.loads(request.content.decode())
        return httpx.Response(200, json={"tool": body["tool"], "result": {"applied": True}})

    return build_mcp_client(handler, server_name="mcp-k8s-write")


@pytest.fixture(autouse=True)
def _reset_kill_switch():  # type: ignore[no-untyped-def]
    executor.set_kill_switch(False)
    yield
    executor.set_kill_switch(False)


# ---- pattern matching ------------------------------------------------------


def test_imagepull_pattern_matches() -> None:
    sel = selfheal.select_action(_state("ImagePullBackOff"), {"imagepull_revert"})
    assert sel is not None
    assert sel[0] == "imagepull_revert" and sel[1].tool == "rollout_undo"


def test_crashloop_pattern_matches() -> None:
    sel = selfheal.select_action(_state("CrashLoopBackOff"), {"crashloop_restart"})
    assert sel is not None and sel[1].tool == "restart_pod"


def test_nothing_enabled_by_default() -> None:
    assert frozenset() == selfheal.DEFAULT_ENABLED
    assert selfheal.select_action(_state("ImagePullBackOff"), selfheal.DEFAULT_ENABLED) is None


def test_disabled_pattern_never_matches() -> None:
    # Category matches but the pattern isn't enabled → no action.
    assert selfheal.select_action(_state("ImagePullBackOff"), {"crashloop_restart"}) is None


def test_non_matching_category_no_action() -> None:
    assert selfheal.select_action(_state("OOMKilled"), {"imagepull_revert"}) is None


# ---- autonomous execution is still fully gated -----------------------------


@pytest.mark.asyncio
async def test_enabled_pattern_executes_through_policy() -> None:
    mcp = _write_mcp()
    recs = await selfheal.self_heal(
        _state("ImagePullBackOff"), enabled={"imagepull_revert"}, mcp_write=mcp, policy=_POLICY
    )
    await mcp.aclose()
    assert len(recs) == 1
    assert recs[0].tool == "rollout_undo"
    assert recs[0].status == "succeeded"


@pytest.mark.asyncio
async def test_self_heal_is_denied_without_a_policy() -> None:
    # Even autonomous self-heal is default-deny without a policy.
    mcp = _write_mcp()
    recs = await selfheal.self_heal(
        _state("ImagePullBackOff"), enabled={"imagepull_revert"}, mcp_write=mcp, policy=None
    )
    await mcp.aclose()
    assert recs[0].status == "skipped"


@pytest.mark.asyncio
async def test_kill_switch_halts_self_heal() -> None:
    executor.set_kill_switch(True)
    mcp = _write_mcp()
    recs = await selfheal.self_heal(
        _state("CrashLoopBackOff"), enabled={"crashloop_restart"}, mcp_write=mcp, policy=_POLICY
    )
    await mcp.aclose()
    assert recs[0].status == "skipped"


@pytest.mark.asyncio
async def test_no_match_no_execution() -> None:
    mcp = _write_mcp()
    recs = await selfheal.self_heal(
        _state("OOMKilled"),
        enabled={"imagepull_revert", "crashloop_restart"},
        mcp_write=mcp,
        policy=_POLICY,
    )
    await mcp.aclose()
    assert recs == []

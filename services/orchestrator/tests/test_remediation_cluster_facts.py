"""Cluster-fact fetch for the remediation path (Phase 4 gap fix).

Fakes mcp-k8s so blast-radius estimation and pre-state capture read real
DeploymentSummary/PodSummary shapes without a cluster.
"""

from __future__ import annotations

from typing import Any

import pytest
from kubepilot_orch.remediation import cluster_facts
from kubepilot_orch.state import RemediationAction, ServiceKnowledge


class _FakeMCP:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def invoke(self, tool: str, arguments: dict[str, Any] | None = None) -> Any:
        self.calls.append((tool, arguments or {}))
        val = self._responses.get(tool)
        if isinstance(val, Exception):
            raise val
        return val


def _action(tool: str, **args: Any) -> RemediationAction:
    return RemediationAction(
        tool=tool,
        target="deployment/checkout",
        namespace="prod",
        arguments=args,
        reversibility="reversible",
        approval_tier="operator",
    )


@pytest.mark.asyncio
async def test_estimate_blast_radius_uses_live_replicas() -> None:
    mcp = _FakeMCP(
        {"get_deployments": [{"name": "checkout", "replicas": 6, "ready_replicas": 6}]}
    )
    br = await cluster_facts.estimate_blast_radius(_action("rollout_restart"), mcp)
    assert br.pods_affected == 6  # whole workload
    assert br.traffic_percent == 100.0


@pytest.mark.asyncio
async def test_estimate_blast_radius_includes_dependents() -> None:
    mcp = _FakeMCP({"get_deployments": [{"name": "checkout", "replicas": 3, "ready_replicas": 3}]})
    knowledge = [ServiceKnowledge(service="checkout", dependents=["web-frontend"])]
    br = await cluster_facts.estimate_blast_radius(_action("rollout_undo"), mcp, knowledge)
    assert br.dependents == ["web-frontend"]


@pytest.mark.asyncio
async def test_estimate_blast_radius_fails_soft_on_read_error() -> None:
    mcp = _FakeMCP({"get_deployments": RuntimeError("mcp down")})
    br = await cluster_facts.estimate_blast_radius(_action("scale", replicas=5), mcp)
    # No facts → conservative estimate, never an exception.
    assert br.pods_affected >= 0


@pytest.mark.asyncio
async def test_capture_pre_state_scale_snapshots_replicas() -> None:
    mcp = _FakeMCP({"get_deployments": [{"name": "checkout", "replicas": 4, "ready_replicas": 4}]})
    pre = await cluster_facts.capture_pre_state(_action("scale", replicas=8), mcp)
    assert pre == {"replicas": 4}


@pytest.mark.asyncio
async def test_capture_pre_state_patch_image_snapshots_image() -> None:
    mcp = _FakeMCP(
        {
            "list_pods": [
                {"containers": [{"name": "checkout", "image": "checkout:v1.2.3"}]},
            ]
        }
    )
    action = _action("patch_image", container="checkout", image="checkout:v2")
    pre = await cluster_facts.capture_pre_state(action, mcp)
    assert pre == {"image": "checkout:v1.2.3", "container": "checkout"}


@pytest.mark.asyncio
async def test_capture_pre_state_none_for_uninvertible_tool() -> None:
    mcp = _FakeMCP({})
    assert await cluster_facts.capture_pre_state(_action("rollout_restart"), mcp) is None

"""Blast-radius estimator — conservative impact + dependents (Phase 4 W3)."""

from __future__ import annotations

from kubepilot_orch.remediation.blast_radius import estimate
from kubepilot_orch.state import RemediationAction, ServiceKnowledge


def _action(
    tool: str, target: str = "deployment/checkout-service", **args: object
) -> RemediationAction:
    return RemediationAction(tool=tool, target=target, namespace="prod", arguments=dict(args))


_KNOWLEDGE = [
    ServiceKnowledge(service="checkout-service", dependents=["web-frontend", "mobile-bff"]),
]


def test_workload_wide_action_hits_all_pods_and_full_traffic() -> None:
    br = estimate(_action("rollout_undo"), current_pods=4, knowledge=_KNOWLEDGE)
    assert br.pods_affected == 4
    assert br.traffic_percent == 100.0
    assert br.dependents == ["web-frontend", "mobile-bff"]
    assert "checkout-service" in br.summary


def test_restart_pod_is_single_pod() -> None:
    br = estimate(_action("restart_pod"), current_pods=4)
    assert br.pods_affected == 1
    assert br.traffic_percent == 25.0  # 1 of 4


def test_scale_counts_only_the_delta() -> None:
    br = estimate(_action("scale", replicas=6), current_pods=4, current_replicas=4)
    assert br.pods_affected == 2  # |6 - 4|
    # 2 of 4 baseline → 50% conservative traffic impact.
    assert br.traffic_percent == 50.0


def test_scale_to_zero_is_full_impact() -> None:
    br = estimate(_action("scale", replicas=0), current_pods=3, current_replicas=3)
    assert br.pods_affected == 3
    assert br.traffic_percent == 100.0


def test_cordon_uses_node_pods_and_is_conservative() -> None:
    br = estimate(_action("cordon", target="node/ip-10-0-1-5"), current_pods=0, node_pods=12)
    assert br.pods_affected == 12
    assert br.traffic_percent == 100.0


def test_no_knowledge_means_no_dependents() -> None:
    br = estimate(_action("rollout_restart"), current_pods=2, knowledge=[])
    assert br.dependents == []
    assert br.pods_affected == 2
